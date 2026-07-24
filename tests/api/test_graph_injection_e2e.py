"""#633 P4 — the graph block reaches a real KB chat turn, over HTTP.

The unit tests prove the block renders and respects its limits. This proves the
wiring: a question naming something the graph knows arrives at the agent with
that thing's facts already attached — nobody asked the model to look it up —
and the message we PERSIST is still exactly what the user typed.
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.normalize import norm_attribute, norm_surface
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim, GraphMention, mention_id
from workspace_app.resources.kb import EMBED_DIM, Collection, KbChat
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _seed(spec) -> None:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        mrm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id="deck-A",
                surface="回焊爐",
                norm_surface=norm_surface("回焊爐"),
                kind="機台",
                norm_kind=norm_surface("機台"),
                occurrences=1,
                chunk_ids=["deck-A#0"],
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id("deck-A", "回焊爐"),
        )
    crm2 = spec.get_resource_manager(GraphClaim)
    with crm2.using("bob"):
        crm2.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                chunk_id="deck-A#0",
                norm_subject=norm_surface("回焊爐"),
                subject="回焊爐",
                norm_attribute=norm_attribute("POR recipe"),
                attribute="POR recipe",
                value="PPOOIXUX",
                norm_value=norm_surface("PPOOIXUX"),
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        )
    link_identical_mentions(spec)


def test_a_question_naming_a_known_thing_arrives_with_its_facts():
    spec = make_spec(default_user=lambda: "bob")
    _seed(spec)
    seen: list[str] = []

    class _Recorder(ScriptedAgentRunner):
        def run(self, prompt, ctx):  # type: ignore[override]
            seen.append(prompt)
            return super().run(prompt, ctx)

    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Recorder([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: "bob",
    )
    client = TestClient(app)
    chat_id = client.post("/kb/chats", json={"title": "t", "collection_ids": []}).json()[
        "resource_id"
    ]
    client.post(f"/kb/chats/{chat_id}/messages", json={"content": "回焊爐是什麼?"})

    assert seen, "the runner never saw a turn"
    assert "PPOOIXUX" in seen[0]  # the fact rode along, unasked
    chat = spec.get_resource_manager(KbChat).get(chat_id).data
    assert chat.messages[0].content == "回焊爐是什麼?"  # persisted clean


def test_a_question_naming_nothing_known_is_untouched():
    spec = make_spec(default_user=lambda: "bob")
    _seed(spec)
    seen: list[str] = []

    class _Recorder(ScriptedAgentRunner):
        def run(self, prompt, ctx):  # type: ignore[override]
            seen.append(prompt)
            return super().run(prompt, ctx)

    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Recorder([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: "bob",
    )
    client = TestClient(app)
    chat_id = client.post("/kb/chats", json={"title": "t", "collection_ids": []}).json()[
        "resource_id"
    ]
    client.post(f"/kb/chats/{chat_id}/messages", json={"content": "今天天氣如何?"})

    assert seen == ["今天天氣如何?"]
