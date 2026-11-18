"""P: the MCP face of a tool bundle (#674).

The same tool, reachable two ways: the platform runs it in a sandbox, and an
engineer's own agent speaks MCP to it. One adapter serves every tool, because
the 3-stage contract already carries what MCP asks for — a list of commands
with schemas, and a way to call one.

The adapter ships inside the bundle and runs on the bundle's own interpreter,
so it uses nothing but the standard library.
"""

from __future__ import annotations

import json
from pathlib import Path

from workspace_app.tooling.mcp_server import handle, load_about, load_tools


def _bundle(tmp_path: Path) -> Path:
    """A bundle's frozen metadata, exactly as `prebuild` leaves it."""
    (tmp_path / "schemas").mkdir()
    (tmp_path / "commands.json").write_text(
        json.dumps([{"name": "count", "description": "Count lines."}])
    )
    (tmp_path / "schemas" / "count.json").write_text(
        json.dumps(
            {
                "name": "count",
                "description": "Count lines.",
                "params_json_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        )
    )
    return tmp_path


def test_tools_are_read_from_what_the_build_already_froze(tmp_path: Path) -> None:
    # The bundle carries its own metadata, so listing costs no subprocess and
    # cannot disagree with what the platform was told about the same tool.
    (tool,) = load_tools(_bundle(tmp_path))

    assert tool["name"] == "count"
    assert tool["description"] == "Count lines."
    assert tool["inputSchema"]["properties"]["path"]["type"] == "string"


def test_initialize_answers_with_a_protocol_version_and_the_tool_capability() -> None:
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, tools=[], invoke=None)

    assert reply is not None
    assert reply["id"] == 1
    assert "protocolVersion" in reply["result"]
    assert "tools" in reply["result"]["capabilities"]


def test_a_notification_is_answered_with_silence() -> None:
    # JSON-RPC: no id means no reply. Sending one anyway breaks strict clients.
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, [], None) is None


def test_listing_returns_the_tools_the_bundle_declares(tmp_path: Path) -> None:
    tools = load_tools(_bundle(tmp_path))

    reply = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tools, invoke=None)

    assert reply is not None
    assert [t["name"] for t in reply["result"]["tools"]] == ["count"]


def test_calling_a_tool_runs_it_and_returns_what_it_printed(tmp_path: Path) -> None:
    tools = load_tools(_bundle(tmp_path))
    seen: list[tuple[str, str]] = []

    def invoke(name: str, args_json: str):
        seen.append((name, args_json))
        return 0, '{"lines": 3}', ""

    reply = handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "count", "arguments": {"path": "a.txt"}},
        },
        tools,
        invoke,
    )

    assert seen == [("count", '{"path": "a.txt"}')]
    assert reply is not None
    assert reply["result"]["content"] == [{"type": "text", "text": '{"lines": 3}'}]
    assert reply["result"].get("isError") in (None, False)


def test_a_failing_tool_is_an_error_result_carrying_our_guidance(tmp_path: Path) -> None:
    """The exit-code contract is the tool's, not the sandbox's — so it means
    the same thing here. A retryable failure has to reach the agent as advice
    it can act on, not as a bare exit status."""
    tools = load_tools(_bundle(tmp_path))

    reply = handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "count", "arguments": {}},
        },
        tools,
        invoke=lambda *_: (2, "", "no such file in the workspace: a.txt"),
    )

    assert reply is not None
    assert reply["result"]["isError"] is True
    text = reply["result"]["content"][0]["text"]
    assert "no such file" in text
    assert "again" in text.lower()  # the retryable guidance travelled with it


def test_an_unknown_tool_is_refused_without_running_anything(tmp_path: Path) -> None:
    tools = load_tools(_bundle(tmp_path))

    reply = handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        },
        tools,
        invoke=lambda *_: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert reply is not None
    assert reply["result"]["isError"] is True
    assert "nope" in reply["result"]["content"][0]["text"]


def test_an_unknown_method_gets_a_json_rpc_error_not_a_crash() -> None:
    reply = handle({"jsonrpc": "2.0", "id": 6, "method": "resources/list"}, [], None)

    assert reply is not None
    assert reply["error"]["code"] == -32601  # method not found


def test_the_real_invoker_runs_the_bundles_own_launcher(tmp_path: Path, monkeypatch) -> None:
    """The seam's default. The integration test drives it for real; this pins
    what it passes, because a launcher called with the wrong argv fails in a
    way that looks like the tool is broken."""
    import subprocess

    from workspace_app.tooling import mcp_server

    seen: list[list[str]] = []

    class _Done:
        returncode, stdout, stderr = 0, "ok", ""

    # `monkeypatch`, not assignment: `subprocess` is shared, and a patch left
    # behind reaches every test that runs after this one.
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append(argv) or _Done())

    invoke = mcp_server._launcher_invoke(tmp_path / "launch")

    assert invoke("count", '{"path":"a"}') == (0, "ok", "")
    assert seen == [[str(tmp_path / "launch"), "count", '{"path":"a"}']]


def test_a_tool_that_hangs_is_stopped_rather_than_hanging_the_agent(tmp_path: Path) -> None:
    """`_GUIDANCE` has an entry for 124 — the platform's timeout — and on this
    path nothing could ever produce one. A tool that blocks took the agent
    with it, with no message and nothing to read.

    The number is the platform's, so the failure reads the same wherever
    somebody meets it."""
    import sys

    from workspace_app.tooling import mcp_server

    entry = tmp_path / "launch"
    entry.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    entry.chmod(0o755)

    invoke = mcp_server._launcher_invoke(entry, timeout=0.5)
    code, _out, err = invoke("count", "{}")

    assert code == 124
    assert "time" in err.lower()


def test_the_timeout_reaches_the_agent_as_the_platform_words_it(tmp_path: Path) -> None:
    tools = load_tools(_bundle(tmp_path))

    reply = handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "count", "arguments": {}},
        },
        tools,
        invoke=lambda *_: (124, "", "timed out"),
    )

    assert reply is not None
    assert "time limit" in reply["result"]["content"][0]["text"]


# ── docs/plan-tool-title.md D10: who the server is ─────────────────────────

_ABOUT = {
    "name": "wafer-history",
    "version": "1.4.2",
    "title": "晶圓歷史查詢",
    "description": "晶圓路徑與良率歷史查詢。",
}


def _initialize(version: str | None, about: dict | None = _ABOUT) -> dict:
    params = {"protocolVersion": version, "capabilities": {}} if version else {}
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
    reply = handle(msg, tools=[], invoke=None, about=about)
    assert reply is not None
    return reply["result"]


def test_a_current_client_reads_the_tools_title_and_description() -> None:
    """`serverInfo.title` exists from MCP 2025-06-18 (`Implementation extends
    BaseMetadata`); `instructions` from 2025-03-26."""
    result = _initialize("2025-06-18")

    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"] == {
        "name": "wafer-history",
        "version": "1.4.2",
        "title": "晶圓歷史查詢",
    }
    assert result["instructions"] == "晶圓路徑與良率歷史查詢。"


def test_an_older_client_gets_its_own_version_and_nothing_it_does_not_know() -> None:
    """Lifecycle: a version the server supports MUST be answered with itself.
    2024-11-05 has neither `title` nor `instructions`."""
    result = _initialize("2024-11-05")

    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"] == {"name": "wafer-history", "version": "1.4.2"}
    assert "instructions" not in result


def test_a_version_this_server_does_not_speak_is_answered_with_its_latest() -> None:
    """2025-03-26 adds JSON-RPC batching, which this server does not serve, so
    it is not claimed: the lifecycle rule is to answer with another supported
    version, the latest."""
    assert _initialize("2025-03-26")["protocolVersion"] == "2025-06-18"
    assert _initialize("1999-01-01")["protocolVersion"] == "2025-06-18"


def test_a_tool_that_gave_no_title_or_description_says_neither() -> None:
    about = {"name": "wafer-history", "version": "1.4.2"}
    result = _initialize("2025-06-18", about)

    assert result["serverInfo"] == {"name": "wafer-history", "version": "1.4.2"}
    assert "instructions" not in result


def test_a_bundle_built_before_titles_still_names_a_server() -> None:
    """No `about.json`: what every bundle in the field answered before."""
    result = _initialize("2025-06-18", None)

    assert result["serverInfo"] == {"name": "tool-bundle", "version": "1"}


def test_about_is_read_from_the_bundle_and_absent_when_it_is(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    assert load_about(bundle) is None

    (bundle / "about.json").write_text(json.dumps(_ABOUT), encoding="utf-8")
    assert load_about(bundle) == _ABOUT
