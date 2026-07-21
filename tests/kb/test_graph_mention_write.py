"""#534 B — persisting one document's mentions, idempotently.

Same contract as the claim writer: re-extracting a document wipes what it wrote
before and writes fresh, so tuning the prompt and re-running never accumulates.
What differs is that occurrences are AGGREGATED — a document mentioning the same
thing on five slides is one row carrying a count of five and five chunk ids, not
five rows. The count is an importance signal the vocabulary layer will use, and
it only means anything if it is gathered per document rather than per passage.
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar import QB
from specstar.types import Binary

from workspace_app.kb.graph.mention_write import write_doc_mentions
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphMention, mention_id
from workspace_app.resources.kb import Collection, SourceDoc


class _FakeLlm(ILlm):
    """One reply per chunk, in order."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._replies.pop(0) if self._replies else "[]"), False


def _deck(spec, cid: str = "c1", doc_id: str = "deck-A") -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        crm.create(Collection(name="c"), resource_id=cid)
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            ),
            resource_id=doc_id,
        )
    return doc_id


def _mentions(spec, doc: str) -> list[GraphMention]:
    rm = spec.get_resource_manager(GraphMention)
    out = []
    for r in rm.list_resources((QB["source_doc_id"] == doc).build()):
        assert isinstance(r.data, GraphMention)
        out.append(r.data)
    return out


def test_the_same_thing_on_several_slides_is_one_row_with_a_count():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    llm = _FakeLlm(
        '[{"surface": "回焊爐", "kind": "機台"}, {"surface": "錫膏", "kind": "材料"}]',
        '[{"surface": "回焊爐", "kind": "機台"}]',
    )
    write_doc_mentions(
        spec,
        llm,
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t0"), ("deck-A#1", "t1")],
    )
    got = {m.surface: m for m in _mentions(spec, "deck-A")}
    assert set(got) == {"回焊爐", "錫膏"}
    assert got["回焊爐"].occurrences == 2
    assert sorted(got["回焊爐"].chunk_ids) == ["deck-A#0", "deck-A#1"]
    assert got["錫膏"].occurrences == 1


def test_surface_variants_land_on_one_row_and_keep_the_first_spelling():
    """The key merges typing noise; the row keeps a readable surface. Which one it
    keeps is arbitrary — what matters is that it is one of the ones the document
    actually used, never a normalised form nobody wrote."""
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    llm = _FakeLlm(
        '[{"surface": "Reflow Oven", "kind": "tool"},'
        ' {"surface": "  reflow   oven ", "kind": "tool"}]'
    )
    write_doc_mentions(
        spec, llm, collection_id="c1", source_doc_id="deck-A", chunks=[("deck-A#0", "t")]
    )
    (got,) = _mentions(spec, "deck-A")
    assert got.occurrences == 2
    assert got.surface == "Reflow Oven"


def test_re_extraction_replaces_rather_than_accumulates():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_mentions(
        spec,
        _FakeLlm('[{"surface": "回焊爐", "kind": "機台"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    write_doc_mentions(
        spec,
        _FakeLlm('[{"surface": "錫膏", "kind": "材料"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    assert [m.surface for m in _mentions(spec, "deck-A")] == ["錫膏"]


def test_a_row_keeps_its_id_across_a_re_run():
    """The id is content-addressed, so the vocabulary's links survive re-extraction
    — the invariant the whole two-layer split rests on."""
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    for _ in range(2):
        write_doc_mentions(
            spec,
            _FakeLlm('[{"surface": "回焊爐", "kind": "機台"}]'),
            collection_id="c1",
            source_doc_id="deck-A",
            chunks=[("deck-A#0", "t")],
        )
    rm = spec.get_resource_manager(GraphMention)
    assert rm.get(mention_id("deck-A", "回焊爐")).data is not None


def test_every_row_carries_the_decks_permission_mirror():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_mentions(
        spec,
        _FakeLlm('[{"surface": "回焊爐", "kind": "機台"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    (got,) = _mentions(spec, "deck-A")
    assert got.collection_created_by == "bob"
    assert got.doc_visibility == "public"


def test_a_vanished_deck_is_wiped_and_skipped():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_mentions(
        spec,
        _FakeLlm('[{"surface": "回焊爐", "kind": "機台"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    spec.get_resource_manager(SourceDoc).permanently_delete("deck-A")
    n = write_doc_mentions(
        spec,
        _FakeLlm('[{"surface": "回焊爐", "kind": "機台"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "t")],
    )
    assert n == 0
    assert _mentions(spec, "deck-A") == []
