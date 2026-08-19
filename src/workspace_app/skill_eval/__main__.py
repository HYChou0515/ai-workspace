"""CLI glue for the skill evaluator — the second-party tuning loop.

Guidance is model-dependent: a skill body tuned against one model is not tuned
against the next. So whoever deploys a skill has to be able to edit it and see
what changed, against their own model and their own data. The shape mirrors
``workspace_app.card_preview``, which solves the same problem for the context-card
prompts:

    # 1. get the shipped guidance as a file you can edit
    python -m workspace_app.skill_eval --dump-skill verify-number -o ./tune

    # 2. score it against your scenarios, with the no-skill control beside it
    python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
        --scenarios docs/skill-eval/verify-number --model ollama_chat/qwen3:14b \
        --control -o ./tune/run-1

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
    p.add_argument("--model", default="ollama_chat/qwen3:14b", help="any litellm model id")
    p.add_argument("--num-ctx", type=int, default=0, metavar="N", help="ollama context window")
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
    p.add_argument("--app", default="rca", help="whose system prompt the turn starts with")
    return p.parse_args(argv)


def _litellm_chat(model: str, num_ctx: int, timeout: int) -> Chat:
    import litellm

    def chat(messages: list[dict], tools: list[dict]) -> Turn:
        extra = {"num_ctx": num_ctx} if num_ctx else {}
        resp = litellm.completion(
            model=model,
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
    chat = _litellm_chat(args.model, args.num_ctx, args.timeout)
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
                skill_name=name,
                skill_md=body,
                app_slug=args.app,
                max_steps=args.max_steps,
            )
            (work / "_transcript.json").write_bytes(msgspec.json.format(msgspec.json.encode(t)))
            if arm == "skill":
                rows.append(row_for(s, t))
            else:
                control_rows[s.name] = row_for(s, t)

    report = Report(skill=name, model=args.model, rows=rows)
    text = render(report, control=control_rows or None)
    (args.out_dir / "report.json").write_bytes(msgspec.json.format(msgspec.json.encode(report)))
    (args.out_dir / "report.txt").write_text(text + "\n")
    print("\n" + text)
    sys.exit(0 if report.passed == len(rows) else 1)


if __name__ == "__main__":
    main()
