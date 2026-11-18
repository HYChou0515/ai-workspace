"""The MCP face of a tool bundle (#674).

The platform runs a tool inside a sandbox. This lets an engineer's own agent —
Claude Code, opencode, codex — run the same tool on their own machine, so a
tool published for the workspace is useful outside it too.

**One adapter serves every tool.** The 3-stage contract already carries what
MCP asks for: a list of commands, a JSON schema per command, and a way to call
one. So there is nothing per-tool here and nothing for an author to write; the
builder copies this file into every bundle.

It uses only the standard library, because it runs on the interpreter the
bundle ships. Adding an SDK would mean adding a dependency to every author's
lock file and every 150MB artifact, to speak a handful of JSON-RPC methods.

The exit-code contract is the tool's, not the sandbox's, so it means the same
thing here: a failure arrives as an MCP error carrying the same guidance an
agent would get on the platform.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

#: The revisions of MCP this speaks, latest first. Only `initialize`,
#: `tools/list` and `tools/call` are implemented — a tool bundle has no
#: resources or prompts to offer, and answering methods we do not serve would
#: be a lie. 2025-03-26 is left out on purpose: it adds JSON-RPC batching, which
#: this server does not serve (2025-06-18 removed it again).
SUPPORTED_VERSIONS = ("2025-06-18", "2024-11-05")
PROTOCOL_VERSION = SUPPORTED_VERSIONS[0]

#: Who the tool is, written by the build beside the bundle's other metadata
#: (docs/plan-tool-title.md D10): the adapter runs inside the bundle and never
#: sees the manifest.
ABOUT_FILE = "about.json"

METHOD_NOT_FOUND = -32601

#: Mirrors `workspace_app.agent.exit_codes`, restated rather than imported: this
#: file runs inside a bundle with no access to the platform. The wording is
#: deliberately the same, so the same failure reads the same way wherever an
#: agent meets it.
_GUIDANCE = {
    2: "This is retryable — call it again, fixing any argument the message names.",
    3: (
        "Someone has to act before this can work, so calling it again unchanged "
        "will fail the same way. Tell the user what the message asks for."
    ),
    124: "The tool ran past its time limit. A smaller request may finish.",
}

#: `(name, args_json) -> (exit_code, stdout, stderr)`. Seamed so the protocol
#: can be tested without spawning anything.
Invoke = Callable[[str, str], tuple[int, str, str]]


def load_tools(bundle: Path) -> list[dict]:
    """The bundle's commands, in MCP's shape.

    Read from the metadata the build already froze rather than by running the
    contract: it costs no subprocess, and it cannot disagree with what the
    platform was told about the same tool."""
    listed = json.loads((bundle / "commands.json").read_text("utf-8"))
    tools = []
    for entry in listed:
        schema = json.loads((bundle / "schemas" / f"{entry['name']}.json").read_text("utf-8"))
        tools.append(
            {
                "name": entry["name"],
                "description": schema.get("description", entry.get("description", "")),
                "inputSchema": schema["params_json_schema"],
            }
        )
    return tools


def load_about(bundle: Path) -> dict | None:
    """``{name, version, title?, description?}`` from the bundle, or ``None``
    for a bundle built before the build wrote it."""
    path = bundle / ABOUT_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text("utf-8"))


def _negotiate(requested: object) -> str:
    """MCP lifecycle: a version the server supports MUST be answered with
    itself; otherwise with another it supports.

    Which other: the newest one no newer than the client asked for, and the
    latest only when none is. A client asking for 2025-03-26 does not know
    2025-06-18 and may disconnect on it, while 2024-11-05 is what it accepted
    from this server before it learnt any other version (review round 1).
    Versions are ISO dates, so they compare as strings."""
    if not isinstance(requested, str):
        # Outside the spec (the client MUST name one), so keep what this
        # server answered everyone before it knew a second version.
        return SUPPORTED_VERSIONS[-1]
    if requested in SUPPORTED_VERSIONS:
        return requested
    older = [v for v in SUPPORTED_VERSIONS if v <= requested]
    return max(older) if older else PROTOCOL_VERSION


def _initialized(version: str, about: dict | None) -> dict:
    if about is None:
        # What every bundle built before `about.json` has always answered.
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tool-bundle", "version": "1"},
        }
    server = {"name": about["name"], "version": about["version"]}
    result: dict = {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": server}
    # Only what the agreed version defines: `Implementation.title` arrived in
    # 2025-06-18; `InitializeResult.instructions` is in every version served
    # here (2024-11-05 already has it — schema/2024-11-05/schema.ts).
    if version == "2025-06-18" and about.get("title"):
        server["title"] = about["title"]
    if about.get("description"):
        result["instructions"] = about["description"]
    return result


def _result(request_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _failed(request_id: object, text: str) -> dict:
    # An MCP error result, not a JSON-RPC error: the call was well-formed and
    # the agent should read what went wrong and decide, rather than treat it as
    # a broken server.
    return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": True})


def handle(
    message: dict, tools: list[dict], invoke: Invoke | None, about: dict | None = None
) -> dict | None:
    """Answer one JSON-RPC message, or None when none is owed."""
    method = message.get("method")
    request_id = message.get("id")

    # No id means a notification: JSON-RPC forbids a reply, and strict clients
    # break when they get one anyway.
    if request_id is None:
        return None

    if method == "initialize":
        params = message.get("params")
        # Before this read anything, `params` could be anything; reading it
        # must not be a way to take the server down.
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return _result(request_id, _initialized(_negotiate(requested), about))

    if method == "tools/list":
        return _result(request_id, {"tools": tools})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        known = {t["name"] for t in tools}
        if name not in known:
            return _failed(
                request_id, f"no such tool: {name!r}; this bundle offers {sorted(known)}"
            )

        assert invoke is not None  # a call reaches here only with a live invoker
        exit_code, stdout, stderr = invoke(name, json.dumps(params.get("arguments") or {}))
        if exit_code == 0:
            return _result(request_id, {"content": [{"type": "text", "text": stdout}]})

        detail = stderr.strip() or stdout.strip() or f"exited {exit_code}"
        guidance = _GUIDANCE.get(exit_code)
        return _failed(request_id, f"{detail}\n\n{guidance}" if guidance else detail)

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": METHOD_NOT_FOUND, "message": f"unsupported method: {method}"},
    }


#: How long a command may run before it is stopped, in seconds. The same
#: figure the platform gives a tool, so an author who fits inside it there
#: fits inside it here.
TIMEOUT_SECONDS = 60.0


def _launcher_invoke(launcher: Path, timeout: float = TIMEOUT_SECONDS) -> Invoke:
    def invoke(name: str, args_json: str) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(  # noqa: S603
                [str(launcher), name, args_json],
                capture_output=True,
                text=True,
                check=False,
                # Without this a tool that blocks takes the agent with it, and
                # `_GUIDANCE`'s entry for 124 could never be reached — the
                # platform's number for "we stopped it", so the failure reads
                # the same wherever somebody meets it.
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, "", f"the tool ran past its {timeout:g}s time limit and was stopped"
        return proc.returncode, proc.stdout, proc.stderr

    return invoke


def main(argv: list[str]) -> int:  # pragma: no cover - exercised by `handle`
    """Serve MCP over stdio for the bundle this file sits in."""
    bundle = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parent
    tools = load_tools(bundle)
    about = load_about(bundle)
    invoke = _launcher_invoke(bundle / "launch")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = handle(message, tools, invoke, about)
        if reply is not None:
            print(json.dumps(reply), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via `main`
    raise SystemExit(main(sys.argv[1:]))
