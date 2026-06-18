"""#106 P6 — the KB chat turn pre-scans the user message against the thread's
context cards and injects matched cards into the agent's turn (so a term it
covers is answered without a kb_search round-trip). The user's persisted message
stays clean; only the content handed to the agent is augmented.
"""

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox


class _RecordingRunner:
    """Captures the content the turn handed the agent — that's where injection
    is observable."""

    def __init__(self) -> None:
        self.prompt: str | None = None

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.prompt = prompt
        yield MessageDelta(text="ok")
        yield RunDone()


def _client(runner: object) -> TestClient:
    app = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,  # ty: ignore[invalid-argument-type]
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app)


def _collection(client: TestClient, name: str = "c") -> str:
    return client.post("/kb/collections", json={"name": name}).json()["resource_id"]


def test_matched_card_is_injected_into_the_agent_turn():
    runner = _RecordingRunner()
    client = _client(runner)
    cid = _collection(client)
    client.post(
        "/context-card/author",
        json={
            "collection_id": cid,
            "keys": ["M4"],
            "title": "Metal 4",
            "body": "the capping layer over metal four",
        },
    )
    chat = client.post("/kb/chats", json={"title": "t", "collection_ids": [cid]}).json()[
        "resource_id"
    ]
    client.post(f"/kb/chats/{chat}/messages", json={"content": "what is M4?"})

    assert runner.prompt is not None
    assert "the capping layer over metal four" in runner.prompt  # the card was injected
    assert "what is M4?" in runner.prompt  # the user's question is preserved
    # the user's persisted message stays clean (no injected block)
    msgs = client.get(f"/kb/chats/{chat}").json()["messages"]
    assert msgs[0]["content"] == "what is M4?"


def test_no_card_match_leaves_the_message_clean():
    runner = _RecordingRunner()
    client = _client(runner)
    cid = _collection(client)
    client.post("/context-card/author", json={"collection_id": cid, "keys": ["M4"], "body": "x"})
    chat = client.post("/kb/chats", json={"title": "t", "collection_ids": [cid]}).json()[
        "resource_id"
    ]
    client.post(f"/kb/chats/{chat}/messages", json={"content": "hello there"})

    assert runner.prompt == "hello there"  # nothing matched → no injection
