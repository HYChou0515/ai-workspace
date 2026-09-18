"""The tool surface a skill is evaluated against, backed by a plain directory.

This is a DOUBLE of the app's tool layer, and it is honest about which parts of
that contract it models:

  modelled      the tool set an RCA turn actually gets; ``exec`` output framed
                like ``agent.tools._format_exec`` (exit-code header, stderr
                dropped when the command succeeded, middle-truncated at the
                configured cap); one tool call per response (``apps/_base.md``);
                paths relative to the workspace root
  NOT modelled  specstar, the sandbox jail, per-item uid/cgroup isolation, SSE
                streaming, the workspace quota, tool authorisation

So a green run here licenses a live check; it does not replace one. The parts it
skips make the real app *more* forgiving, not less — an eval that passes here and
fails there is a bug worth knowing about, not a false alarm.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import msgspec

#: agent/tools.py caps exec output at ``exec.output_max_chars`` (30_000). The
#: 200_000 ``tool_output_max_chars`` is the SDK-wide backstop, a different knob.
EXEC_CAP = 30_000


class Event(msgspec.Struct, frozen=True):
    """Something the run did that a scenario can be scored on."""

    kind: str
    detail: str


def truncate_middle(text: str, cap: int) -> str:
    """Head + tail with the middle elided, as ``agent.output_cap`` does."""
    if len(text) <= cap:
        return text
    head = cap * 2 // 3
    tail = cap - head
    omitted = len(text) - cap
    return f"{text[:head]}\n… [{omitted} chars omitted — narrow the command] …\n{text[-tail:]}"


def schemas() -> list[dict]:
    """OpenAI-style tool schemas. Descriptions are kept close to the real
    docstrings, because the description is part of the guidance under test."""

    def fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        }

    s = {"type": "string"}
    return [
        fn(
            "write_file",
            "Write a text file in the workspace, creating or replacing it.",
            {"path": s, "content": s},
            ["path", "content"],
        ),
        fn("read_file", "Read a text file from the workspace.", {"path": s}, ["path"]),
        fn(
            "list_files",
            "List one level of the workspace. Directories end with '/'.",
            {"path": s},
            [],
        ),
        fn(
            "exec",
            "Run a shell command inside your workspace sandbox. Pass the command as a "
            'list of arguments, e.g. ["python", "analysis.py"].',
            {"cmd": {"type": "array", "items": s}},
            ["cmd"],
        ),
        fn(
            "show_file",
            "Show a workspace file to the user in the chat — an image renders "
            "inline. Use it for any chart or file the user should look at.",
            {"path": s, "caption": s},
            ["path"],
        ),
        fn(
            "ask_user",
            "Ask the user a question and stop this turn. Use it only for a decision "
            "you cannot make yourself.",
            {"question": s},
            ["question"],
        ),
        # The standing-instruction tools. Offered because the scenarios that
        # tune `author-skill` / `author-workflow` / `skill-hub` score on
        # WHETHER the model reaches for them — a harness that never offered
        # them could not pass those scenarios in either arm, so a run measured
        # nothing (review round 1 of plan-skill-hub). Their doubles write the
        # file where the real tool would, or answer as the real tool answers;
        # none of them touches a hub or a reviewer.
        fn(
            "save_skill",
            "Save a reusable skill into THIS workspace so you (and the user) can load "
            "it later with `read_skill`. `name` is a short title, `description` a one-line "
            "'when to use this', `body` the methodology in markdown.",
            {"name": s, "description": s, "body": s},
            ["name", "description", "body"],
        ),
        fn(
            "save_workflow",
            "Validate and save a workspace workflow (`workflow.json`) under "
            "`.workflows/<slug>/`. `body` is the JSON text.",
            {"name": s, "body": s},
            ["name", "body"],
        ),
        fn(
            "save_schedules",
            "Validate and save the item's `.workflows/schedules.json` — which of the "
            "item's workflows run on a clock. `body` is the JSON text.",
            {"body": s},
            ["body"],
        ),
        fn(
            "search_skill_hub",
            "Find skills other users have published to the skill hub. `query` matches "
            "the name and description; an empty query lists everything. Each hit shows "
            "`owner/name`, the description, which App it was written in, and the entry "
            "id that `install_skill` takes.",
            {"query": s},
            ["query"],
        ),
        fn(
            "install_skill",
            "Install a skill from the skill hub into THIS workspace, as `.skill/<name>/`, "
            "so it is loadable with `read_skill` from the next turn on. Use it after the "
            "user has picked an entry (by id) and said to install it.",
            {"entry_id": s},
            ["entry_id"],
        ),
        fn(
            "publish_skill",
            "Publish one of THIS workspace's skills (a `.skill/<name>/` folder) to the "
            "skill hub, where every user of the platform can find it and install it. "
            "Use it when the user asks to share, publish or upload a skill — and only "
            "after they have said which one.",
            {"name": s},
            ["name"],
        ),
    ]


def run(name: str, args: dict, work: Path, events: list[Event]) -> str:
    """Execute one tool call against ``work`` and return what the model sees."""
    if name == "write_file":
        target = work / args["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        content = args.get("content", "")
        target.write_text(content)
        return f"wrote {args['path']} ({len(content)} bytes)"
    if name == "read_file":
        target = work / args["path"]
        if not target.is_file():
            return f"no such file: {args['path']}"
        return truncate_middle(target.read_text(), EXEC_CAP)
    if name == "list_files":
        base = work / args.get("path", ".")
        if not base.is_dir():
            return f"no such directory: {args.get('path', '.')}"
        return "\n".join(sorted(f"{c.name}/" if c.is_dir() else c.name for c in base.iterdir()))
    if name == "exec":
        return _exec(list(args["cmd"]), work)
    if name == "show_file":
        target = work / args["path"]
        events.append(Event("show_file", args["path"]))
        if not target.is_file():
            return f"no such file: {args['path']}"
        return f"shown to the user: {args['path']}"
    if name == "ask_user":
        events.append(Event("ask_user", args["question"]))
        return "(the question was put to the user; this turn ends here)"
    if name == "save_skill":
        slug = "-".join(
            w for w in "".join(c if c.isalnum() else " " for c in args["name"].lower()).split()
        )
        target = work / ".skill" / slug / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"---\nname: {slug}\ndescription: {args['description']}\n---\n\n{args['body']}\n"
        )
        return (
            f"saved skill '{slug}' to .skill/{slug}/SKILL.md. "
            f"Load it any time with read_skill('{slug}')."
        )
    if name == "save_workflow":
        target = work / ".workflows" / args["name"] / "workflow.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["body"])
        return f"saved workflow '{args['name']}' to .workflows/{args['name']}/workflow.json"
    if name == "save_schedules":
        target = work / ".workflows" / "schedules.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["body"])
        return "saved .workflows/schedules.json"
    if name == "search_skill_hub":
        # A fixed listing: the scoring asks whether the model SEARCHED, never
        # what it found. One hit in another App, so the告知 line has something
        # to relay.
        return (
            f"1 skill hub entry matches {args['query']!r}:\n"
            "- alice/reflow-triage — Triage reflow defects from the log. "
            "[written in pm; id 0123456789abcdef0123456789abcdef]\n"
            "    mentions query_entity, which this App lacks"
        )
    if name == "install_skill":
        return (
            f"installed skill 'reflow-triage' (by alice, written in the pm App) into "
            f".skill/reflow-triage/ (entry {args['entry_id']}). It is in the skill index "
            "from the next turn on; load it any time with read_skill('reflow-triage')."
        )
    if name == "publish_skill":
        return (
            f"published skill '{args['name']}' to the skill hub (new; entry "
            "0123456789abcdef0123456789abcdef).\n"
            "The reviewer (eval) had nothing to flag.\n"
            "It is public: everyone on the platform can find and install it."
        )
    return f"unknown tool {name!r}"


def _exec(cmd: list[str], work: Path) -> str:
    # The sandbox has ONE python and `pip` installs into it; here that is the
    # interpreter running the eval, so a script's imports resolve the same way.
    if cmd and cmd[0] in ("python", "python3"):
        cmd = [sys.executable, *cmd[1:]]
    try:
        done = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return "Tool `exec` returned (exit_code=127):\ncommand not found"
    except subprocess.TimeoutExpired:
        return "Tool `exec` returned (exit_code=124):\ntimed out"
    body = done.stdout if done.returncode == 0 else f"{done.stdout}\n--- stderr ---\n{done.stderr}"
    return f"Tool `exec` returned (exit_code={done.returncode}):\n{truncate_middle(body, EXEC_CAP)}"
