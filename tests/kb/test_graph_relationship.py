"""#534 B — the connections, stored as evidence.

A relationship is what one document said connects two things. Like a mention it
is never rewritten, keyed on what said it so a re-run lands on the same row, and
carries its document's read permission — it repeats a sentence's content, so it
is exactly as visible as the sentence.
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar import QB
from specstar.types import Binary, ResourceIDNotFoundError

from workspace_app.kb.graph.doc_write import write_doc_graph
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.kb.llm import ILlm
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphRelationship
from workspace_app.resources.kb import Collection, SourceDoc


class _FakeLlm(ILlm):
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._replies.pop(0) if self._replies else "{}"), False


_REPLY = (
    '{"mentions": [{"surface": "回焊爐", "kind": "機台"}, {"surface": "空洞", "kind": "缺陷"}],'
    ' "relationships": [{"subject": "回焊爐", "predicate": "造成", "object": "空洞",'
    ' "quote": "回焊爐溫度過高造成空洞"}]}'
)


def _deck(spec, *, private: bool = False) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="c", permission=Permission(visibility="private") if private else None),
            resource_id="c1",
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="private" if private else "public",
                collection_created_by="bob",
            ),
            resource_id="deck-A",
        )
    return cid


def _relationships(spec) -> list[GraphRelationship]:
    rm = spec.get_resource_manager(GraphRelationship)
    out = []
    for r in rm.list_resources(QB.all().build()):
        assert isinstance(r.data, GraphRelationship)
        out.append(r.data)
    return out


def test_a_stated_connection_is_stored_with_its_ends_and_provenance():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_graph(
        spec,
        _FakeLlm(_REPLY),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "回焊爐溫度過高造成空洞")],
    )
    (rel,) = _relationships(spec)
    assert (rel.subject, rel.predicate, rel.object) == ("回焊爐", "造成", "空洞")
    assert rel.norm_subject == norm_surface("回焊爐")
    assert rel.norm_predicate == norm_surface("造成")
    assert rel.chunk_id == "deck-A#0"
    assert rel.quote == "回焊爐溫度過高造成空洞"


def test_re_extraction_replaces_rather_than_accumulates():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    for _ in range(2):
        write_doc_graph(
            spec,
            _FakeLlm(_REPLY),
            collection_id="c1",
            source_doc_id="deck-A",
            chunks=[("deck-A#0", "回焊爐溫度過高造成空洞")],
        )
    assert len(_relationships(spec)) == 1


def test_a_connection_is_as_visible_as_the_document_that_stated_it():
    """It repeats a sentence's content — including the sentence itself — so it
    rides the same scope every other piece of evidence does."""
    import pytest

    spec = make_spec(default_user=lambda: "bob")
    _deck(spec, private=True)
    write_doc_graph(
        spec,
        _FakeLlm(_REPLY),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "回焊爐溫度過高造成空洞")],
    )
    rm = spec.get_resource_manager(GraphRelationship)
    (row,) = list(rm.list_resources(QB.all().build()))
    rid = row.info.resource_id  # ty: ignore[unresolved-attribute]
    with rm.using("bob", apply_access_scope=True):  # ty: ignore[unknown-argument]
        assert rm.get(rid).data is not None
    with (
        rm.using("alice", apply_access_scope=True),  # ty: ignore[unknown-argument]
        pytest.raises(ResourceIDNotFoundError),
    ):
        rm.get(rid)


def test_a_deleted_deck_takes_its_connections_with_it():
    from workspace_app.kb.graph.mention_write import wipe_doc_mentions

    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_graph(
        spec,
        _FakeLlm(_REPLY),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "回焊爐溫度過高造成空洞")],
    )
    wipe_doc_mentions(spec, "deck-A")
    assert _relationships(spec) == []
