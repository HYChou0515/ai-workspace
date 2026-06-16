"""#62: a tool run that fails by writing to stderr while exiting 0 used to
flash an error live, then settle into a clean "returned (exit_code=0)" — the
stderr the user saw was dropped from the final card. The fix decouples the
two surfaces: the LLM keeps the cleaned result (stderr dropped on success),
while the FE/persistence gets the FULL result (stderr kept) so the error
doesn't vanish.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from specstar import QB

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import _exec_result_text
from workspace_app.api import RunDone, ScriptedAgentRunner, ToolEnd, ToolStart, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ExecResult

from .conftest import register_rca_item


def test_exec_result_text_returns_cleaned_and_records_full_display_on_success_stderr():
    """A command exits 0 but wrote to stderr: the LLM-facing return drops
    the stderr (noise), and the full display (with stderr) is stashed on the
    context keyed by the cleaned text for the runner to attach."""
    ctx = AgentToolContext()
    result = ExecResult(exit_code=0, stdout=b"done", stderr=b"ERROR: connection refused")
    cleaned = _exec_result_text(ctx, "exec", result)

    assert "ERROR: connection refused" not in cleaned
    assert cleaned in ctx.tool_displays
    assert "ERROR: connection refused" in ctx.tool_displays[cleaned]
    assert "exit_code=0" in ctx.tool_displays[cleaned]


def test_exec_result_text_records_nothing_when_clean_success():
    """No stderr → cleaned == display, so nothing is stashed (the map stays
    empty and the FE just renders the output)."""
    ctx = AgentToolContext()
    result = ExecResult(exit_code=0, stdout=b"done", stderr=b"")
    _exec_result_text(ctx, "exec", result)
    assert ctx.tool_displays == {}


def test_exec_result_text_records_nothing_on_failure_because_cleaned_already_has_stderr():
    """On failure the cleaned result already includes stderr, so display ==
    cleaned and there's nothing extra to stash."""
    ctx = AgentToolContext()
    result = ExecResult(exit_code=1, stdout=b"", stderr=b"boom")
    _exec_result_text(ctx, "exec", result)
    assert ctx.tool_displays == {}


def test_tool_end_display_persists_to_the_message_for_the_fe():
    """The full display result rides ToolEnd.display through the turn engine
    and persists on the tool Message's `tool_display`, while `content` keeps
    the cleaned LLM-facing form."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    cleaned = "Tool `exec` returned (exit_code=0):\ndone"
    full = "Tool `exec` returned (exit_code=0):\ndone\n--- stderr ---\nERROR: connection refused"
    runner = ScriptedAgentRunner(
        [
            ToolStart(call_id="c1", name="exec", args={"cmd": ["curl", "x"]}),
            ToolEnd(call_id="c1", output=cleaned, display=full),
            RunDone(),
        ]
    )
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool_msg = next(m for m in conv.messages if m.role == "tool")
    # LLM-facing content stays cleaned; the FE display keeps the stderr.
    assert tool_msg.content == cleaned
    assert "ERROR: connection refused" in tool_msg.tool_display


def test_tool_end_without_display_leaves_message_display_empty():
    """The common case (no separate display) persists an empty tool_display,
    so the FE falls back to `content`."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    runner = ScriptedAgentRunner(
        [
            ToolStart(call_id="c1", name="exec", args={"cmd": ["ls"]}),
            ToolEnd(call_id="c1", output="Tool `exec` returned (exit_code=0):\nok"),
            RunDone(),
        ]
    )
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.item_id == iid
    )
    tool_msg = next(m for m in conv.messages if m.role == "tool")
    assert tool_msg.tool_display == ""
