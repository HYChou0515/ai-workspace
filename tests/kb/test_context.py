"""plan-rag-context P2 — the pure context-expansion core."""

from workspace_app.kb.context import ChunkSpan, expand_passages
from workspace_app.resources.kb import RetrievedPassage

# One document whose canonical text is 30 chars; three overlapping chunks the
# way a sliding-window chunker really cuts them.
_TEXT = "0123456789abcdefghijABCDEFGHIJ"
_CHUNKS = [
    ChunkSpan(chunk_id="d1#0", doc_id="d1", seq=0, start=0, end=10),
    ChunkSpan(chunk_id="d1#1", doc_id="d1", seq=1, start=8, end=20),
    ChunkSpan(chunk_id="d1#2", doc_id="d1", seq=2, start=18, end=30),
]


def _hit_on_middle_chunk() -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c",
        document_id="d1",
        filename="d1.md",
        start=8,
        end=20,
        source_chunk_ids=["d1#1"],
        text=_TEXT[8:20],
        score=1.0,
    )


def _single_doc(passages, *, min_chars):
    return expand_passages(
        passages,
        min_chars=min_chars,
        chunks_of=lambda _doc: _CHUNKS,
        text_of=lambda _doc: _TEXT,
        neighbours=lambda _doc: (None, None),
        label_of=lambda doc: doc,
    )


def test_expands_to_whole_neighbouring_chunks_and_keeps_the_hit_span():
    [p] = _single_doc([_hit_on_middle_chunk()], min_chars=5)
    # The hit is untouched: citation / highlight / snippet still point at it.
    assert (p.start, p.end, p.text) == (8, 20, _TEXT[8:20])
    # Context reaches one whole chunk each side — never a mid-chunk cut.
    assert p.context_text == _TEXT[0:30]


def test_zero_means_off():
    [p] = _single_doc([_hit_on_middle_chunk()], min_chars=0)
    assert p.context_text == ""


def test_at_least_n_takes_whole_chunks_until_met_then_stops():
    # Five 10-char chunks, no overlap, hit on the middle one ([20, 30)).
    text = "".join(str(i) * 10 for i in range(5))  # "000…111…222…333…444…"
    spans = [
        ChunkSpan(chunk_id=f"d#{i}", doc_id="d", seq=i, start=i * 10, end=i * 10 + 10)
        for i in range(5)
    ]
    hit = RetrievedPassage(
        collection_id="c",
        document_id="d",
        filename="d.md",
        start=20,
        end=30,
        source_chunk_ids=["d#2"],
        text=text[20:30],
    )

    def run(min_chars):
        [p] = expand_passages(
            [hit],
            min_chars=min_chars,
            chunks_of=lambda _d: spans,
            text_of=lambda _d: text,
            neighbours=lambda _d: (None, None),
            label_of=lambda d: d,
        )
        return p

    # N=3: one 10-char chunk each side already satisfies it — take exactly one.
    assert (run(3).context_start, run(3).context_end) == (10, 40)
    # N=15: one chunk (10) is short, two (20) is enough — take exactly two.
    assert (run(15).context_start, run(15).context_end) == (0, 50)
    assert run(15).context_text == text


def _one_chunk_doc(d: str) -> list[ChunkSpan]:
    return [ChunkSpan(chunk_id=f"{d}#0", doc_id=d, seq=0, start=0, end=10)]


def test_walks_into_the_neighbouring_documents_when_this_one_runs_out():
    # Three single-chunk documents in tree order a → b → c; the hit is all of b.
    texts = {"a": "A" * 10, "b": "B" * 10, "c": "C" * 10}
    order = ["a", "b", "c"]

    def neighbours(d):
        i = order.index(d)
        return (order[i - 1] if i > 0 else None, order[i + 1] if i + 1 < len(order) else None)

    hit = RetrievedPassage(
        collection_id="c",
        document_id="b",
        filename="b.md",
        start=0,
        end=10,
        source_chunk_ids=["b#0"],
        text=texts["b"],
    )
    [p] = expand_passages(
        [hit],
        min_chars=5,
        chunks_of=_one_chunk_doc,
        text_of=lambda d: texts[d],
        neighbours=neighbours,
        label_of=lambda d: f"{d}.md",
    )
    # b has nothing before or after the hit, so the context spills into a and c.
    # A boundary line names each document so the model can tell it crossed a
    # file (the hit's own document is labelled too, once something precedes it).
    assert p.context_text == "AAAAAAAAAA\n\n── b.md ──\n\nBBBBBBBBBB\n\n── c.md ──\n\nCCCCCCCCCC"
    # The same-document range is just the hit — the spill has no offset here.
    assert (p.context_start, p.context_end) == (0, 10)


def test_stops_at_the_edge_of_the_collection():
    hit = RetrievedPassage(
        collection_id="c",
        document_id="a",
        filename="a.md",
        start=0,
        end=10,
        source_chunk_ids=["a#0"],
        text="A" * 10,
    )
    [p] = expand_passages(
        [hit],
        min_chars=100,
        chunks_of=_one_chunk_doc,
        text_of=lambda _d: "A" * 10,
        neighbours=lambda _d: (None, None),
        label_of=lambda d: d,
    )
    assert p.context_text == "AAAAAAAAAA"  # nothing to reach; no separators


def test_a_cyclic_neighbour_graph_terminates_at_the_structural_bound():
    # A buggy `neighbours` that points a document at itself must truncate, not
    # hang: the walk is capped per side, so the context has at most that many
    # boundary lines on each side of the hit.
    hit = RetrievedPassage(
        collection_id="c",
        document_id="a",
        filename="a.md",
        start=0,
        end=10,
        source_chunk_ids=["a#0"],
        text="A" * 10,
    )
    [p] = expand_passages(
        [hit],
        min_chars=10**9,
        chunks_of=_one_chunk_doc,
        text_of=lambda _d: "A" * 10,
        neighbours=lambda d: (d, d),
        label_of=lambda d: d,
    )
    assert p.context_text.count("── a ──") == 2 * 32


def test_hits_whose_contexts_overlap_merge_into_one_passage():
    # Five 10-char chunks; hits on chunk 1 and chunk 3. Alone they are 10 chars
    # apart, but with N=5 each context reaches the chunk between them, so the
    # two would deliver the same text twice. They merge: one passage whose hit
    # span is the union (the `merge.py` convention for nearby hits), whose
    # source ids are both, and whose context is computed once for the union.
    text = "".join(str(i) * 10 for i in range(5))
    spans = [
        ChunkSpan(chunk_id=f"d#{i}", doc_id="d", seq=i, start=i * 10, end=i * 10 + 10)
        for i in range(5)
    ]

    def hit(i, score):
        return RetrievedPassage(
            collection_id="c",
            document_id="d",
            filename="d.md",
            start=i * 10,
            end=i * 10 + 10,
            source_chunk_ids=[f"d#{i}"],
            text=text[i * 10 : i * 10 + 10],
            score=score,
            provenance={"page": [i]},
        )

    out = expand_passages(
        [hit(3, 0.9), hit(1, 0.4)],
        min_chars=5,
        chunks_of=lambda _d: spans,
        text_of=lambda _d: text,
        neighbours=lambda _d: (None, None),
        label_of=lambda d: d,
    )
    assert len(out) == 1
    [p] = out
    assert (p.start, p.end) == (10, 40)
    assert p.text == text[10:40]
    assert p.source_chunk_ids == ["d#1", "d#3"]  # document order, both kept
    assert p.score == 0.9  # the stronger hit's score survives
    assert p.provenance == {"page": [1, 3]}
    assert (p.context_start, p.context_end) == (0, 50)
    assert p.context_text == text


def test_hits_whose_contexts_do_not_touch_stay_separate():
    text = "".join(str(i) * 10 for i in range(9))
    spans = [
        ChunkSpan(chunk_id=f"d#{i}", doc_id="d", seq=i, start=i * 10, end=i * 10 + 10)
        for i in range(9)
    ]

    def hit(i):
        return RetrievedPassage(
            collection_id="c",
            document_id="d",
            filename="d.md",
            start=i * 10,
            end=i * 10 + 10,
            source_chunk_ids=[f"d#{i}"],
            text=text[i * 10 : i * 10 + 10],
        )

    # Hits on 1 and 7 with N=5: contexts [0,30) and [60,90) — a 30-char gap.
    out = expand_passages(
        [hit(1), hit(7)],
        min_chars=5,
        chunks_of=lambda _d: spans,
        text_of=lambda _d: text,
        neighbours=lambda _d: (None, None),
        label_of=lambda d: d,
    )
    assert [(p.context_start, p.context_end) for p in out] == [(0, 30), (60, 90)]
