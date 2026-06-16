"""Citation counting (point 1): per-marker rule, doc != sum(chunk)."""

from specstar import SpecStar

from workspace_app.kb.cited import (
    chunk_cited,
    collection_cited,
    doc_cited,
    doc_cited_for_ids,
    record_citations,
)
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Citation


def _spec() -> SpecStar:
    s = make_spec(default_user="u")
    return s


def _cite(marker: int, doc: str, chunks: list[str], coll: str = "c1") -> Citation:
    return Citation(
        marker=marker,
        collection_id=coll,
        document_id=doc,
        filename="f.md",
        start=0,
        end=1,
        source_chunk_ids=chunks,
    )


def test_empty_log_has_no_counts():
    s = _spec()
    assert collection_cited(s) == {}
    assert doc_cited(s) == {}
    assert chunk_cited(s, "d1") == {}


def test_one_citation_credits_doc_collection_once_and_each_chunk():
    s = _spec()
    # one [n] whose merged passage spanned two (overlapping) chunks of d1
    record_citations(
        s, [_cite(1, "d1", ["d1#0", "d1#1"])], origin_kind="kb_chat", origin_id="chat", cited_by="u"
    )
    assert doc_cited(s) == {"d1": 1}
    assert collection_cited(s) == {"c1": 1}
    assert chunk_cited(s, "d1") == {"d1#0": 1, "d1#1": 1}
    # the whole point: doc is NOT the sum of its chunk counts
    assert doc_cited(s)["d1"] != sum(chunk_cited(s, "d1").values())


def test_per_marker_not_deduped_within_an_answer():
    s = _spec()
    # same answer cites d1 twice → +2, not collapsed to 1
    record_citations(
        s,
        [_cite(1, "d1", ["d1#0"]), _cite(2, "d1", ["d1#0"])],
        origin_kind="kb_chat",
        origin_id="chat",
        cited_by="u",
    )
    assert doc_cited(s)["d1"] == 2
    assert chunk_cited(s, "d1")["d1#0"] == 2
    assert collection_cited(s)["c1"] == 2


def test_doc_cited_for_ids_scopes_to_the_requested_docs():
    # A page renders ≤ N docs of one collection — it needs the cited counts for
    # THOSE docs, not a global group-by over the whole citation log. Scoping to
    # the page's ids excludes other cited docs (here d3).
    s = _spec()
    for doc, n in (("d1", 2), ("d2", 1), ("d3", 5)):
        record_citations(
            s,
            [_cite(i, doc, [f"{doc}#0"]) for i in range(n)],
            origin_kind="kb_chat",
            origin_id="chat",
            cited_by="u",
        )
    assert doc_cited_for_ids(s, ["d1", "d2"]) == {"d1": 2, "d2": 1}  # d3 excluded


def test_doc_cited_for_ids_empty_list_skips_the_query():
    s = _spec()
    record_citations(
        s, [_cite(1, "d1", ["d1#0"])], origin_kind="kb_chat", origin_id="chat", cited_by="u"
    )
    assert doc_cited_for_ids(s, []) == {}
