"""POST /health/replay/turn + /health/replay/doc (#51 P4).

The four replay entry points (diagnostics / doc / turn / tool-call) all
land on these two endpoints. The turn endpoint serves both the RCA
conversation and KB chat threads (`source`); the doc endpoint serves
chat-export and image documents. Replay never executes a tool and never
writes state — asserted here by the fakes (a scripted completion / KB
LLM, no sandbox, no pipeline).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.health.replay import ReplayService
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.llm import ILlm
from workspace_app.resources import Conversation, Message, make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _chunk(content=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))]
    )


class _FakeCompletion:
    def __init__(self, chunks):
        self._chunks = chunks
        self.kwargs: dict = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return iter(self._chunks)


class _FakeLlm(ILlm):
    def __init__(self, response: str) -> None:
        self._response = response

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._response, False)


def _client(replay: ReplayService | None, spec: SpecStar | None = None) -> TestClient:
    from workspace_app.kb.li_pipeline import build_doc_pipeline

    embedder = HashEmbedder(dim=EMBED_DIM)
    app = create_app(
        spec=spec or make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=embedder,
        # Pipeline mode so uploads of ANY type are stored (the doc
        # replay tests upload .chat.json / .md).
        kb_pipeline=build_doc_pipeline(embedder=embedder),
        replay_service=replay,
    )
    return TestClient(app)


def _seed_rca_thread(spec: SpecStar) -> str:
    """An RCA item with a finished turn: user → tool → assistant."""
    inv = spec.get_resource_manager(RcaInvestigation).create(
        RcaInvestigation(title="MX-7 voids", owner="alice", description="d")
    )
    rid = inv.resource_id
    spec.get_resource_manager(Conversation).create(
        Conversation(
            item_id=rid,
            messages=[
                Message(role="user", content="check the oven log"),
                Message(
                    role="tool",
                    content="zone3: 412C",
                    tool_call_id="c1",
                    tool_name="read_file",
                    tool_args={"path": "oven.log"},
                ),
                Message(role="assistant", content="Zone 3 ran hot."),
            ],
        )
    )
    return rid


def test_replay_rca_turn_returns_fresh_output_and_the_original():
    """Tracer bullet: replaying the assistant answer of an RCA turn
    probes the current model with the rebuilt context and echoes the
    persisted original for side-by-side comparison."""
    completion = _FakeCompletion([_chunk("Zone 3 exceeded "), _chunk("its limit.")])
    spec = make_spec()
    client = _client(ReplayService(completion=completion), spec)
    rid = _seed_rca_thread(spec)

    resp = client.post(
        "/health/replay/turn",
        json={"source": "rca", "thread_id": rid, "message_index": 2},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Zone 3 exceeded its limit."
    assert body["original"]["role"] == "assistant"
    assert body["original"]["content"] == "Zone 3 ran hot."
    # The probe saw the turn's history — including the tool exchange.
    sent = completion.kwargs["messages"]
    assert any(m.get("role") == "tool" for m in sent)
    # Pure probe: model output only, nothing was persisted.
    conv = spec.get_resource_manager(Conversation)
    from specstar import QB

    [row] = conv.list_resources((QB["item_id"] == rid).build())
    assert len(row.data.messages) == 3  # ty: ignore[unresolved-attribute]


def test_replay_kb_chat_turn_uses_the_default_kb_agent():
    """KB chat threads replay through the same endpoint with source="kb";
    the probe runs the deploy's default KB agent config (the per-message
    model pick isn't persisted)."""
    from workspace_app.resources.kb import KbChat, KbMessage

    completion = _FakeCompletion([_chunk("F14 is the Tainan fab.")])
    spec = make_spec()
    client = _client(ReplayService(completion=completion), spec)
    chat = spec.get_resource_manager(KbChat).create(
        KbChat(
            title="fab terms",
            messages=[
                KbMessage(role="user", content="what is F14?"),
                KbMessage(role="assistant", content="A fab."),
            ],
        )
    )

    resp = client.post(
        "/health/replay/turn",
        json={"source": "kb", "thread_id": chat.resource_id, "message_index": 1},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "F14 is the Tainan fab."
    assert body["original"]["content"] == "A fab."
    # The KB agent's tool (kb_search) was on the probe's menu.
    names = {t["function"]["name"] for t in completion.kwargs["tools"]}
    assert "kb_search" in names
    # #69 observability: the route echoes WHAT it sent so the operator can
    # compare against the live turn's logged trace.
    req = body["request"]
    assert "kb_search" in req["tools"]
    assert req["parallel_tool_calls"] == "unset"
    assert req["tool_choice"] == "auto (unset)"


def test_replay_doc_reruns_extraction_on_an_uploaded_chat_export():
    """Doc-level replay: the stored blob (not a re-upload) is pushed
    back through the extraction prompt; the raw response + parse note
    come back. Nothing is re-indexed."""
    import json as _json

    raw = '{"insights": [{"kind": "context", "title": "Fab", "markdown": "F14."}]}'
    client = _client(ReplayService(completion=_FakeCompletion([]), kb_llm=_FakeLlm(raw)))
    with client:
        cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
        blob = _json.dumps(
            {"title": "Oven RCA", "messages": [{"role": "user", "content": "why?"}]}
        ).encode()
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": ("inv-1.chat.json", blob, "application/json")},
        )
        [doc] = client.get(f"/kb/collections/{cid}/documents").json()["items"]

        resp = client.post("/health/replay/doc", json={"document_id": doc["resource_id"]})

        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == raw
        assert "1 insight" in body["note"]


def test_replay_error_codes():
    """404 unknown thread/doc, 422 non-replayable message, 409 doc with
    no AI step, 503 when the deploy has no replay service."""
    spec = make_spec()
    client = _client(ReplayService(completion=_FakeCompletion([])), spec)
    with client:
        rid = _seed_rca_thread(spec)
        # Unknown ids → 404.
        body = {"source": "rca", "thread_id": "nope", "message_index": 0}
        assert client.post("/health/replay/turn", json=body).status_code == 404
        assert client.post("/health/replay/doc", json={"document_id": "nope"}).status_code == 404
        # A user message has no LLM interaction behind it → 422.
        body = {"source": "rca", "thread_id": rid, "message_index": 0}
        assert client.post("/health/replay/turn", json=body).status_code == 422
        # A plain markdown doc never touched an LLM → 409.
        cid = client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
        client.post(
            f"/kb/collections/{cid}/documents",
            files={"file": ("a.md", b"hello", "text/markdown")},
        )
        [doc] = client.get(f"/kb/collections/{cid}/documents").json()["items"]
        resp = client.post("/health/replay/doc", json={"document_id": doc["resource_id"]})
        assert resp.status_code == 409

    # No service wired → 503, never a crash.
    bare = _client(None)
    body = {"source": "rca", "thread_id": "x", "message_index": 0}
    assert bare.post("/health/replay/turn", json=body).status_code == 503
    assert bare.post("/health/replay/doc", json={"document_id": "x"}).status_code == 503
