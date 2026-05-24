from workspace_app.kb.citations import parse_citations
from workspace_app.resources.kb import RetrievedPassage


def _passage(doc: str, text: str, chunks: list[str]) -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c1",
        document_id=doc,
        filename=doc.split("/")[-1],
        start=0,
        end=len(text),
        source_chunk_ids=chunks,
        text=text,
    )


def test_parses_marker_into_citation_from_registry():
    passages = [_passage("c1/u/a.md", "alpha passage", ["c1/u/a.md#0"])]
    cites = parse_citations("The cause was alpha [1].", passages)
    assert len(cites) == 1
    c = cites[0]
    assert c.marker == 1
    assert c.document_id == "c1/u/a.md"
    assert c.filename == "a.md"
    assert c.source_chunk_ids == ["c1/u/a.md#0"]
    assert c.snippet == "alpha passage"


def test_multiple_markers_deduped_and_ordered():
    passages = [
        _passage("c1/u/a.md", "alpha", ["a#0"]),
        _passage("c1/u/b.md", "beta", ["b#0"]),
    ]
    cites = parse_citations("First [2], then [1], and again [2].", passages)
    assert [c.marker for c in cites] == [1, 2]  # deduped, ascending


def test_out_of_range_and_zero_markers_ignored():
    passages = [_passage("c1/u/a.md", "alpha", ["a#0"])]
    cites = parse_citations("see [0], [1], [5]", passages)
    assert [c.marker for c in cites] == [1]  # 0 and 5 have no passage


def test_no_markers_yields_no_citations():
    assert parse_citations("a plain answer with no brackets", []) == []
