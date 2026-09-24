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

from ..apps.frontmatter import FrontmatterError, parse_frontmatter
from ..apps.shared_skills import PLUGIN_SKILLS, SHARED_SKILLS, shared_skill_source
from ..apps.skill_payload import skill_payload
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
        help="a registered shared skill by NAME, or a path to a SKILL.md (its frontmatter "
        "`name` says whose references/ and scripts/ it runs with: the registered skill of "
        "that name, else the file's own folder when that folder is named after the skill)",
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
    known = ", ".join(sorted(settings.agents.presets))
    if preset is not None and preset not in settings.agents.presets:
        raise SystemExit(f"unknown preset {preset!r}. config knows: {known}")
    try:
        cfg = get_app_catalog(settings).resolve(
            app_slug=app_slug, profile=profile, attached_preset=preset
        )
    except KeyError as e:
        raise SystemExit(f"cannot resolve a turn for {app_slug}/{profile} ({e})") from e
    if preset is not None:
        # `resolve` honours an attached preset only when the App's picker (or
        # the profile's subset) lists it, and falls back to the default without
        # a word — right for a live turn, wrong here: a run that measured some
        # other model would report a number about nothing. The preset's model
        # and endpoint are what it contributes; if the turn does not carry
        # them, it is not that preset's turn.
        want = settings.agents.presets[preset]
        if (cfg.model, cfg.llm_base_url) != (want.model, want.llm.base_url):
            raise SystemExit(
                f"preset {preset!r} is not in app {app_slug!r} / profile {profile!r}'s picker, "
                f"so the turn resolved to {cfg.name!r} ({cfg.model}) instead. Name a picker "
                f"preset, or override one of them in the config you pass with --config."
            )
    return cfg


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


def _stage(
    scenario: Scenario, scenarios_dir: Path, work: Path, *, skill: tuple[str, Path] | None
) -> None:
    """The scenario's data at the workspace root, and the skill's OWN files
    (`references/`, `scripts/`, …) under `.skill/<name>/` — the path a real
    turn holds them at (`apps.skills.materialize_skill`), copied through the
    same `skill_payload` so build noise is left behind here too. Without the
    second half a body that says "read `.skill/<name>/references/x.md` first"
    scores the model on a step the workspace made impossible: every such
    `read_file` answered "no such file". `SKILL.md` itself is not staged; the
    body reaches the model through the prompt.

    ``skill`` is ``(name, source folder)`` — the folder is named by the
    registry, not by the skill, so the name is passed rather than read off
    the path. ``None`` is the control arm: a turn that never loaded the skill
    never received its files, so the control workspace holds none either."""
    work.mkdir(parents=True, exist_ok=True)
    for name in scenario.data:
        shutil.copy(scenarios_dir / name, work / name)
    if skill is None:
        return
    name, folder = skill
    for rel, data in skill_payload(folder).items():
        if rel == "SKILL.md":
            continue
        target = work / ".skill" / name / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _resolve_skill(spec: str) -> tuple[str, str, Path]:
    """``(name, SKILL.md text, folder)`` from a registered name or a path to a
    body. The name is the frontmatter's — never the folder's, because
    `--dump-skill … -o ./tune` puts the body in a folder called `tune`. The
    folder is where `references/` and `scripts/` come from, and it is found by
    that name: the registered skill first (the dump writes `SKILL.md` alone,
    so an edited copy never holds better files than the registry does — and
    whatever else sits beside it, a run's output say, is not skill content);
    else the file's own folder when it IS a skill folder, i.e. named `<name>`
    — the invariant the platform's loader holds every profile and workspace
    skill folder to — which is what lets a profile skill or a skill still
    being written run with its own files. A body neither rule can place is
    refused rather than run without its files."""
    path = Path(spec)
    text: str | None = None
    name = spec
    if path.is_file():
        text = path.read_text()
        try:
            front, _body = parse_frontmatter(text.encode())
        except FrontmatterError as e:
            raise SystemExit(f"{path}: {e}") from e
        name = str(front.get("name", "")).strip()
        if not name:
            raise SystemExit(f"{path}: SKILL.md frontmatter has no `name`")
    src = shared_skill_source(name)
    # `.resolve()`: `--skill SKILL.md` from inside the folder has parent `.`,
    # whose own name is "" — the rule is about the folder, not the spelling.
    own = path.resolve().parent
    if src is None and text is not None and own.name == name:
        src = own
    if src is None:
        where = f"{path} is not in a folder named {name!r} and" if text is not None else "it is"
        raise SystemExit(
            f"unknown skill {name!r}: {where} not registered "
            f"(registered: {', '.join(sorted({*SHARED_SKILLS, *PLUGIN_SKILLS}))})"
        )
    return name, text if text is not None else (src / "SKILL.md").read_text(), src


def register_view_plugins(config_path: Path | None) -> None:
    """#847/#848: make the installed view plugins' skills resolvable here, the
    way `create_app` does for a live turn — from the same config, the same dir,
    the same strict discovery. Without it a plugin skill is "unknown skill",
    and the turn's `## Available skills` index would lack it."""
    from ..config.loader import load
    from ..view_plugins.discovery import discover_view_plugins, resolve_plugins_dir
    from ..view_plugins.skills import register_for_agents

    settings = load(config_path=config_path)
    register_for_agents(discover_view_plugins(resolve_plugins_dir(settings.view_plugins)))


def main() -> None:
    args = _parse_args()
    register_view_plugins(args.config)
    if args.dump_skill:
        _name, text, _folder = _resolve_skill(args.dump_skill)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        target = args.out_dir / "SKILL.md"
        target.write_text(text)
        print(f"wrote {target} — edit it, then pass it back with --skill {target}")
        return
    if not args.skill or not args.scenarios:
        raise SystemExit("need --skill and --scenarios (or --dump-skill)")

    name, skill_md, skill_dir = _resolve_skill(args.skill)
    # Named once, because it is not always the folder beside `--skill`: an
    # edited copy of a registered skill runs with the REGISTRY's files.
    print(f"skill {name!r}: references/ and scripts/ from {skill_dir}")
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
            _stage(s, args.scenarios, work, skill=(name, skill_dir) if arm == "skill" else None)
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
