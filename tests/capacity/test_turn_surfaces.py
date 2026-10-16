"""Each surface tells the turn engine what it is and when its message was
accepted (docs/plan-capacity-usage.md §6.3), through its real route: an app
chat send, a KB chat send. `accepted_ms` is the persisted question's own
timestamp — the acceptance — not the moment the turn was queued."""

from __future__ import annotations

import asyncio
import time

from httpx import ASGITransport

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.sink import UsageSink
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ..api._client import AsyncClient
from ..api.conftest import register_rca_item


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


def _setup():
    sink = _ListSink()
    recorder = UsageRecorder(sink, component="api", pod="api-1", window_s=300, queue_max=1000)
    spec = make_spec(default_user="alice")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        get_user_id=lambda: "alice",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        usage_recorder=recorder,
    )
    return app, spec, sink, recorder


async def _turn(sink: _ListSink, recorder: UsageRecorder) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        recorder.flush()
        turns = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "turn"]
        if turns:
            return turns[0]
        await asyncio.sleep(0.05)
    raise AssertionError("no turn was recorded")


async def test_an_app_chat_turn_is_recorded_as_chat_from_its_persisted_question():
    app, spec, sink, recorder = _setup()
    iid = register_rca_item(spec)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        chat = (await c.post(f"/a/rca/items/{iid}/chats", json={"title": "c"})).json()["chat_id"]
        r = await c.post(f"/a/rca/items/{iid}/chats/{chat}/messages", json={"content": "go"})
        assert r.status_code in (200, 202), r.text
        turn = await _turn(sink, recorder)
    _rid, conv = app.state.chat_send._locator.require_chat("rca", iid, chat)  # noqa: SLF001

    question = next(m for m in conv.messages if m.role == "user")
    assert (turn["surface"], turn["user"], turn["app"], turn["item"]) == (
        "chat",
        "alice",
        "rca",
        iid,
    )
    assert turn["accepted_ms"] == question.created_at
    assert turn["outcome"] == "done"


async def test_a_kb_chat_turn_is_recorded_as_kb_from_its_persisted_question():
    app, _spec, sink, recorder = _setup()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={"title": "t", "collection_ids": []})).json()[
            "resource_id"
        ]
        r = await c.post(f"/kb/chats/{cid}/messages", json={"content": "why?"})
        assert r.status_code in (200, 202), r.text
        turn = await _turn(sink, recorder)
        msgs = (await c.get(f"/kb/chats/{cid}")).json()["messages"]

    question = next(m for m in msgs if m["role"] == "user")
    assert turn["surface"] == "kb"
    assert turn["accepted_ms"] == question["created_at"]


async def test_a_workflow_step_turn_is_recorded_as_workflow(monkeypatch):
    """Through `WorkflowExecutor.drive_turn`, the door every workflow step's turn
    takes (captured from `create_app` as `test_cov_fill_r2_workflow_exec` does)."""
    import workspace_app.api.app as app_mod
    from workspace_app.apps.playground.model import PlaygroundItem
    from workspace_app.filestore.specstar_impl import SpecstarFileStore

    sink = _ListSink()
    recorder = UsageRecorder(sink, component="api", pod="api-1", window_s=300, queue_max=1000)
    spec = make_spec()
    captured: dict[str, object] = {}
    real = app_mod.WorkflowExecutor

    def _capture(**kw):
        captured["ex"] = ex = real(**kw)
        return ex

    monkeypatch.setattr(app_mod, "WorkflowExecutor", _capture)
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="ok"), RunDone()]),
        usage_recorder=recorder,
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )

    await captured["ex"].drive_turn(item_id, "no-such-chat", "u", "hello", None)  # ty: ignore[unresolved-attribute]
    turn = await _turn(sink, recorder)

    assert (turn["surface"], turn["item"]) == ("workflow", item_id)
