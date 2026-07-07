"""Issue #103: per-doc chunk counts via a scoped GROUP BY push-down, so the
documents list never materialises chunk bodies just to count them.

#104: a chunk is bound to CONTENT (source_file_id), not to one doc — so a doc's
count is `count(chunk where source_file_id == doc.file_id)`, collection-scoped.
Identical content at several paths shares ONE chunk set, so every backed doc
reports the shared count (not 0). Legacy pre-#104 chunks (source_file_id == "")
have no content key and fall back to counting by source_doc_id — which keeps the
existing corpus's counts correct through the deploy→reindex window.
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.chunk_counts import doc_chunks_for_ids
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, DocChunk


def _spec() -> SpecStar:
    return make_spec(default_user="u")


def _add_chunks(spec: SpecStar, doc: str, n: int, *, coll: str = "c1", file_id: str = "") -> None:
    rm = spec.get_resource_manager(DocChunk)
    for seq in range(n):
        rm.create(
            DocChunk(
                collection_id=coll,
                source_doc_id=doc,
                source_file_id=file_id,
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
    # ids excludes other docs' chunks (here d3). (Legacy chunks: source_file_id
    # == "", so they count via the source_doc_id fallback.)
    s = _spec()
    _add_chunks(s, "d1", 3)
    _add_chunks(s, "d2", 1)
    _add_chunks(s, "d3", 5)
    assert doc_chunks_for_ids(s, "c1", {"d1": "", "d2": ""}) == {"d1": 3, "d2": 1}


def test_doc_chunks_for_ids_empty_input_skips_the_query():
    s = _spec()
    _add_chunks(s, "d1", 2)
    assert doc_chunks_for_ids(s, "c1", {}) == {}


def test_doc_chunks_for_ids_reports_shared_content_count_for_every_backed_doc():
    # #104 Q4: identical content lives at two paths sharing ONE chunk set keyed
    # by file_id "H"; both docs backed by that content report the shared count.
    s = _spec()
    _add_chunks(s, "a", 2, coll="c1", file_id="H")  # the single content chunk set
    assert doc_chunks_for_ids(s, "c1", {"a": "H", "b": "H"}) == {"a": 2, "b": 2}


def test_doc_chunks_for_ids_content_counts_are_collection_scoped():
    # file_id is a GLOBAL content hash, so the same content in another collection
    # must NOT inflate this collection's count.
    s = _spec()
    _add_chunks(s, "a", 2, coll="c1", file_id="H")
    _add_chunks(s, "x", 5, coll="c2", file_id="H")  # same content, other collection
    assert doc_chunks_for_ids(s, "c1", {"a": "H"}) == {"a": 2}


def test_doc_chunks_for_ids_falls_back_to_source_doc_id_for_legacy_chunks():
    # Pre-#104 chunks carry source_file_id == "" (no content key). Counting them
    # must fall back to source_doc_id so the whole existing corpus shows the right
    # counts BEFORE a reindex stamps file_ids.
    s = _spec()
    _add_chunks(s, "legacy", 4, coll="c1", file_id="")
    assert doc_chunks_for_ids(s, "c1", {"legacy": ""}) == {"legacy": 4}
