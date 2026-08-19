"""CLI glue for the skill evaluator — the second-party tuning loop.

Guidance is model-dependent: a skill body tuned against one model is not tuned
against the next. So whoever deploys a skill has to be able to edit it and see
what changed, against their own model and their own data.

The model is never a command-line string. The turn is resolved by
``AppCatalog.resolve`` — the same call a live turn makes — so the model, the
endpoint and the system prompt all come from config.yaml + app.json + the
profile, and ``--preset`` picks another of the App picker's presets.

    # 1. get the shipped guidance as a file you can edit
    python -m workspace_app.skill_eval --dump-skill verify-number -o ./tune

    # 2. score it against your scenarios, with the no-skill control beside it
    python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
        --scenarios sample-scenarios/verify-number --control -o ./tune/run-1

    # 3. edit ./tune/SKILL.md, rerun into run-2, compare the two reports

Settings-driven composition + network IO, omitted from coverage like the other
CLI roots; everything it calls is unit-tested in ``skill_eval``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import msgspec

from ..apps.shared_skills import SHARED_SKILLS
from .report import Report, render, row_for
from .runner import Chat, ToolCall, Transcript, Turn, run_scenario
from .scenario import Scenario, load_scenarios


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m workspace_app.skill_eval",
        description="Run a skill's guidance against scenarios and score it. "
        "Reads only — nothing is stored in the app.",
    )
    p.add_argument(
        "--skill",
        default=None,
        help="a registered shared skill by NAME, or a path to a SKILL.md you edited",
    )
    p.add_argument(
        "--dump-skill",
        default=None,
        metavar="NAME",
        help="write the shipped skill to --out-dir and exit, as a starting point to edit",
    )
    p.add_argument(
        "--scenarios",
        type=Path,
        default=None,
        help="folder of *.json scenarios and the data files they name. See "
        "docs/extending-the-platform.md for the field reference",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("./skill-eval"),
        help="per-scenario workspaces + transcripts + report.json/report.txt land here. "
        "Use a fresh dir per run so two versions can be compared side by side",
    )
    p.add_argument(
        "--preset",
        default=None,
        metavar="NAME",
        help="a preset from config.yaml `agents.presets` — the same names the App's "
        "model picker offers. Default: the App's own first picker entry, so the eval "
        "runs against what that App actually ships with",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="path to config.yaml. Omitted, the bundled defaults apply",
    )
    p.add_argument(
        "--num-ctx",
        type=int,
        default=0,
        metavar="N",
        help="ollama context window for this run. An eval box is often tighter than "
        "the deployment, and a truncated prompt scores the window, not the guidance",
    )
    p.add_argument("--timeout", type=int, default=900, metavar="S", help="per model call, seconds")
    p.add_argument(
        "--control",
        action="store_true",
        help="also run every scenario with NO skill. A scenario the control passes too "
        "is not measuring the guidance — without this the report cannot say so",
    )
    p.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="give up on a scenario after this many model turns; the report says "
        "step-limit rather than pretending the run answered",
    )
    p.add_argument("--app", default="rca", help="whose turn the guidance is evaluated inside")
    p.add_argument("--profile", default="default", help="that App's profile")
    return p.parse_args(argv)


def _resolve_agent(app_slug: str, profile: str, preset: str | None, config_path: Path | None):
    """Ask the app for the turn it would really run.

    ``AppCatalog.resolve`` is the same call a live turn makes, so the model, the
    endpoint and the system prompt all come from config.yaml + app.json + the
    profile — not from a string retyped on the command line. It also carries the
    ``## Available skills`` index, without which ``read_skill`` triggering cannot
    be measured at all.
    """
    from ..config.loader import load
    from ..factories import get_app_catalog

    settings = load(config_path=config_path)
    try:
        return get_app_catalog(settings).resolve(
            app_slug=app_slug, profile=profile, attached_preset=preset
        )
    except KeyError as e:
        known = ", ".join(sorted(settings.agents.presets))
        raise SystemExit(f"unknown preset {preset!r} ({e}). config knows: {known}") from e


def _litellm_chat(cfg, num_ctx: int, timeout: int) -> Chat:
    import litellm

    def chat(messages: list[dict], tools: list[dict]) -> Turn:
        extra = {"num_ctx": num_ctx} if num_ctx else {}
        if cfg.llm_base_url:
            extra["api_base"] = cfg.llm_base_url
        if cfg.llm_api_key:
            extra["api_key"] = cfg.llm_api_key
        resp = litellm.completion(
            model=cfg.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            timeout=timeout,
            **extra,
        )
        m = resp.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, args=json.loads(c.function.arguments or "{}"))
            for c in (m.tool_calls or [])
        ]
        return Turn(content=m.content or "", tool_calls=calls)

    return chat


def _stage(scenario: Scenario, scenarios_dir: Path, work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    for name in scenario.data:
        shutil.copy(scenarios_dir / name, work / name)


def _resolve_skill(spec: str) -> tuple[str, str]:
    """``(name, SKILL.md text)`` from a registered name or a path."""
    path = Path(spec)
    if path.is_file():
        return path.parent.name, path.read_text()
    src = SHARED_SKILLS.get(spec)
    if src is None:
        raise SystemExit(f"unknown skill {spec!r}. registered: {', '.join(sorted(SHARED_SKILLS))}")
    return spec, (src / "SKILL.md").read_text()


def main() -> None:
    args = _parse_args()
    if args.dump_skill:
        _name, text = _resolve_skill(args.dump_skill)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        target = args.out_dir / "SKILL.md"
        target.write_text(text)
        print(f"wrote {target} — edit it, then pass it back with --skill {target}")
        return
    if not args.skill or not args.scenarios:
        raise SystemExit("need --skill and --scenarios (or --dump-skill)")

    name, skill_md = _resolve_skill(args.skill)
    scenarios = load_scenarios(args.scenarios)
    if not scenarios:
        raise SystemExit(f"no *.json scenarios in {args.scenarios}")
    cfg = _resolve_agent(args.app, args.profile, args.preset, args.config)
    chat = _litellm_chat(cfg, args.num_ctx, args.timeout)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows, control_rows = [], {}
    for s in scenarios:
        for arm, body in (("skill", skill_md), *((("control", ""),) if args.control else ())):
            work = args.out_dir / f"{s.name}.{arm}"
            _stage(s, args.scenarios, work)
            print(f"[{arm}] {s.name} …", flush=True)
            t: Transcript = run_scenario(
                chat,
                s,
                work,
                system_prompt=cfg.system_prompt,
                skill_name=name,
                skill_md=body,
                max_steps=args.max_steps,
            )
            (work / "_transcript.json").write_bytes(msgspec.json.format(msgspec.json.encode(t)))
            if arm == "skill":
                rows.append(row_for(s, t))
            else:
                control_rows[s.name] = row_for(s, t)

    # Name BOTH: the preset is what a reader recognises, the model is what was
    # actually called, and a report that only says one of them cannot be reproduced.
    label = f"{args.preset or 'default'} ({cfg.model})"
    report = Report(skill=name, model=label, rows=rows)
    text = render(report, control=control_rows or None)
    (args.out_dir / "report.json").write_bytes(msgspec.json.format(msgspec.json.encode(report)))
    (args.out_dir / "report.txt").write_text(text + "\n")
    print("\n" + text)
    sys.exit(0 if report.passed == len(rows) else 1)


if __name__ == "__main__":
    main()
