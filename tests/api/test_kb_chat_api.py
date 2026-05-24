from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources.kb import EMBED_DIM, RetrievedPassage
from workspace_app.sandbox.mock import MockSandbox


class _KbRunner:
    """Stands in for the KB agent: 'runs' kb_search (filling the per-turn
    passage registry on the context) then answers citing [1]."""

    def __init__(self) -> None:
        self.seen_collections: list[str] | None = None

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.seen_collections = list(ctx.collection_ids)
        ctx.kb_passages.append(
            RetrievedPassage(
                collection_id="c",
                document_id="c/u/reflow.md",
                filename="reflow.md",
                start=0,
                end=16,
                source_chunk_ids=["c/u/reflow.md#0"],
                text="zone three drift",
            )
        )
        yield MessageDelta(text="searching the kb", reasoning=True)  # <think> channel
        yield MessageDelta(text="Zone three drifted ")
        yield MessageDelta(text="[1].")
        yield RunDone()


class _BoomRunner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        raise RuntimeError("model exploded")
        yield  # pragma: no cover — unreachable, makes this an async generator


def _client(runner: object) -> TestClient:
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,  # ty: ignore[invalid-argument-type]
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app)


def test_create_list_get_delete_chat():
    client = _client(_KbRunner())
    cid = client.post("/kb/chats", json={"title": "Reflow", "collection_ids": ["c"]}).json()[
        "resource_id"
    ]

    listed = client.get("/kb/chats").json()
    match = next(c for c in listed if c["resource_id"] == cid)
    assert match["title"] == "Reflow"
    assert match["collection_ids"] == ["c"]

    got = client.get(f"/kb/chats/{cid}").json()
    assert got["messages"] == []

    assert client.delete(f"/kb/chats/{cid}").status_code == 204
    assert client.get(f"/kb/chats/{cid}").status_code == 404


def test_send_message_streams_and_persists_answer_with_citations():
    runner = _KbRunner()
    client = _client(runner)
    cid = client.post("/kb/chats", json={"title": "t", "collection_ids": ["c"]}).json()[
        "resource_id"
    ]

    r = client.post(f"/kb/chats/{cid}/messages", json={"content": "why voids?"})
    assert r.status_code == 200
    body = r.text
    assert "message_delta" in body and "done" in body  # streamed live
    assert runner.seen_collections == ["c"]  # the thread's collections drove retrieval

    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "why voids?"
    answer = msgs[1]
    assert answer["content"] == "Zone three drifted [1]."
    assert answer["reasoning"] == "searching the kb"  # <think> kept separate from content
    # [1] resolved against the registry the run populated
    assert len(answer["citations"]) == 1
    cite = answer["citations"][0]
    assert cite["marker"] == 1
    assert cite["document_id"] == "c/u/reflow.md"
    assert cite["filename"] == "reflow.md"


def test_run_error_is_streamed_and_nothing_persisted():
    client = _client(_BoomRunner())
    cid = client.post("/kb/chats", json={"collection_ids": ["c"]}).json()["resource_id"]

    r = client.post(f"/kb/chats/{cid}/messages", json={"content": "boom?"})
    assert r.status_code == 200
    assert "error" in r.text  # the failure surfaces as a terminal SSE event

    # only the user's message persisted — the failed turn produced no answer
    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]
