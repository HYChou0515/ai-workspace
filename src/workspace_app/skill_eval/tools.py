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
