"""#37 — a failed turn must leave a trace in the conversation.

Before this fix, a turn that errored (or was cancelled, or hit the turn
cap) streamed its error live but persisted NOTHING, so re-entering the
workspace showed only the user's own message — impossible to debug.
Now the failure is persisted as a `role="error"` message (visible on
reload), and `history_items` decides per error_kind whether the model
sees it next turn.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from workspace_app.api import (
    MaxTurnsExceeded,
    MessageDelta,
    RunError,
    ScriptedAgentRunner,
    ToolEnd,
    ToolStart,
    create_app,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from .conftest import register_rca_item


def _client(events) -> tuple[TestClient, object, str]:
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner(events),
    )
    return TestClient(app), spec, register_rca_item(spec)


def _messages(spec, investigation_id: str):
    from specstar import QB

    rm = spec.get_resource_manager(Conversation)
    for r in rm.list_resources((QB["item_id"] == investigation_id).build()):
        return list(r.data.messages)
    return []


def _drain(client: TestClient, inv: str, content: str) -> None:
    with client.stream("POST", f"/a/rca/items/{inv}/messages", json={"content": content}) as r:
        for _ in r.iter_lines():
            pass


def test_errored_turn_persists_an_error_message():
    """A turn that errors before any output still leaves the user's
    message AND an error message behind."""
    client, spec, iid = _client([RunError(message="APIConnectionError: refused")])
    _drain(client, iid, "why is zone 3 hot?")

    msgs = _messages(spec, iid)
    roles = [m.role for m in msgs]
    assert roles == ["user", "error"]
    err = msgs[-1]
    assert err.error_kind == "error"
    assert "refused" in err.content


def test_partial_output_is_kept_with_the_error_marker():
    """When the model streamed a partial answer (and ran a tool) before
    dying, that work is persisted too — only the error marker is added,
    not a wipe."""
    client, spec, iid = _client(
        [
            MessageDelta(text="Looking at the reflow log"),
            ToolStart(call_id="c1", name="read_file", args={"path": "oven.log"}),
            ToolEnd(call_id="c1", output="zone3: 412C"),
            RunError(message="provider 500"),
        ]
    )
    _drain(client, iid, "diagnose")

    roles = [m.role for m in _messages(spec, iid)]
    assert roles == ["user", "assistant", "tool", "error"]


def test_max_turns_persists_a_distinct_kind():
    client, spec, iid = _client([MaxTurnsExceeded(turns=10)])
    _drain(client, iid, "go")

    err = _messages(spec, iid)[-1]
    assert err.role == "error"
    assert err.error_kind == "max_turns"


def test_cancelled_turn_persists_a_trace():
    """A superseded / stopped turn keeps its partial output + a
    cancelled marker. We drive cancellation directly through the engine
    so the test doesn't race a real interrupt."""
    import asyncio

    from workspace_app.agent.context import AgentToolContext
    from workspace_app.api.events import MessageDelta as _Delta
    from workspace_app.api.turns import ChatTurnEngine, TurnMessage

    class _SlowRunner:
        async def run(self, prompt, ctx):
            yield _Delta(text="partial")
            raise asyncio.CancelledError()

    captured: list[list[TurnMessage]] = []
    engine = ChatTurnEngine(_SlowRunner())

    async def go() -> None:
        resp = await engine.stream(
            "k", "q", AgentToolContext(), on_complete=lambda p: captured.append(p)
        )
        async for _ in resp.body_iterator:
            pass

    asyncio.run(go())
    produced = captured[0]
    assert [m.role for m in produced] == ["assistant", "error"]
    assert produced[-1].error_kind == "cancelled"
