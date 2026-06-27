"""Issue #103: per-doc chunk counts via a scoped GROUP BY push-down, so the
documents list never materialises chunk bodies just to count them."""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.chunk_counts import doc_chunks_for_ids
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, DocChunk


def _spec() -> SpecStar:
    return make_spec(default_user="u")


def _add_chunks(spec: SpecStar, doc: str, n: int, *, coll: str = "c1") -> None:
    rm = spec.get_resource_manager(DocChunk)
    for seq in range(n):
        rm.create(
            DocChunk(
                collection_id=coll,
                source_doc_id=doc,
                seq=seq,
                start=0,
                end=1,
                text=f"chunk {seq}",
                embedding=[0.0] * EMBED_DIM,
            )
        )


def test_doc_chunks_for_ids_scopes_to_the_requested_docs():
    # A page renders ≤ N docs of one collection — it needs the chunk counts for
    # THOSE docs, not a scan of every chunk in the store. Scoping to the page's
    # ids excludes other docs' chunks (here d3).
    s = _spec()
    _add_chunks(s, "d1", 3)
    _add_chunks(s, "d2", 1)
    _add_chunks(s, "d3", 5)
    assert doc_chunks_for_ids(s, ["d1", "d2"]) == {"d1": 3, "d2": 1}  # d3 excluded


def test_doc_chunks_for_ids_empty_list_skips_the_query():
    s = _spec()
    _add_chunks(s, "d1", 2)
    assert doc_chunks_for_ids(s, []) == {}
