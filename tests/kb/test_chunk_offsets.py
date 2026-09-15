"""plan-rag-context P8 — `DocChunk.start/end` index `SourceDoc.text`.

The invariant every offset consumer on this branch rests on (the context
walk's `text_of(doc)[lo:hi]`, `kb_grep`'s verify window, `read_page`'s text
layer via `page_text`, the read-registry dedup key): a chunk's span is where
its text sits in the document's CANONICAL text. The pipeline path never
established it, in two ways —

- LlamaIndex offsets are relative to the node's own `Document`, and a
  page-shaped parser emits one Document per page (slide, CSV row, JSONL line),
  so page 2's chunks pointed into page 1's text;
- LlamaIndex locates each piece with `text.find(piece)` — the FIRST
  occurrence — so repetitive text put 36 of 38 chunks inside its first 1.8k
  chars, and the context walk went 18k chars back and 0 forward.

Four reviewers found the first independently; master never noticed because
nothing on master sliced the text at a chunk's offset."""

from __future__ import annotations

import pytest
from agents import RunContextWrapper, ToolOutputText
from specstar import QB, SpecStar

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import kb_grep_impl, read_page_impl
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.index_coordinator import IndexCoordinator
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.retriever import Retriever
from workspace_app.resources import AgentConfig, Collection, DocChunk, SourceDoc, make_spec
from workspace_app.resources.kb import EMBED_DIM

from .pdf_fixture import text_pdf


def _ingestor(spec: SpecStar) -> tuple[Ingestor, HashEmbedder]:
    emb = HashEmbedder(dim=EMBED_DIM)
    # The default registry: `PdfParser()` without a VLM reads the text layer.
    return Ingestor(spec, pipeline=build_doc_pipeline(embedder=emb), embedder=emb), emb


def _collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _chunks(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    rows = [r.data for r in rm.list_resources((QB["source_doc_id"] == doc_id).build())]
    return sorted((c for c in rows if isinstance(c, DocChunk)), key=lambda c: c.seq)


def _text(spec: SpecStar, doc_id: str) -> str:
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc) and doc.text is not None
    return doc.text


def _assert_spans_index_the_text(text: str, chunks: list[DocChunk]) -> None:
    assert chunks
    for c in chunks:
        assert text[c.start : c.end] == c.text, (c.seq, c.start, c.end, c.text[:40])


_PAGES = [
    ["PAGE ONE line 0 alpha", "PAGE ONE line 1 bravo"],
    ["PAGE TWO line 0 charlie", "PAGE TWO line 1 delta echo"],
    ["PAGE THREE line 0 foxtrot", "PAGE THREE line 1 golf"],
]


def test_a_multi_page_pdf_text_layer_gets_canonical_offsets(spec: SpecStar):
    ing, _ = _ingestor(spec)
    cid = _collection(spec)
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="paper.pdf", data=text_pdf(_PAGES))
    chunks = _chunks(spec, doc_id)
    _assert_spans_index_the_text(_text(spec, doc_id), chunks)
    # One Document per page: page 2's chunk starts AFTER page 1's text, not at 0.
    by_page = {c.provenance.get("page"): c for c in chunks}
    assert set(by_page) == {1, 2, 3}
    assert by_page[1].start == 0 < by_page[2].start < by_page[3].start


def test_a_multi_row_csv_gets_canonical_offsets(spec: SpecStar):
    ing, _ = _ingestor(spec)
    cid = _collection(spec)
    body = "part,desc\nA100,widget alpha\nB200,gadget bravo\nC300,gizmo charlie\n"
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="parts.csv", data=body.encode())
    chunks = _chunks(spec, doc_id)
    _assert_spans_index_the_text(_text(spec, doc_id), chunks)
    assert len({c.start for c in chunks}) == len(chunks)  # one row per chunk, no two at 0


def test_repetitive_text_gets_monotonic_offsets(spec: SpecStar):
    # `text.find(piece)` finds the FIRST occurrence; a document that repeats a
    # paragraph put every later chunk back at the top. Offsets are positions:
    # they advance with the chunks.
    ing, _ = _ingestor(spec)
    cid = _collection(spec)
    para = "The same paragraph appears again and again in this report. " * 40
    body = para + "One unique sentence sits near the end. " + para
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="rep.txt", data=body.encode())
    chunks = _chunks(spec, doc_id)
    assert len(chunks) > 3
    _assert_spans_index_the_text(_text(spec, doc_id), chunks)
    starts = [c.start for c in chunks]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_a_dry_run_reparse_gets_the_same_canonical_offsets(spec: SpecStar):
    ing, _ = _ingestor(spec)
    cid = _collection(spec)
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="paper.pdf", data=text_pdf(_PAGES))
    virtual, text = ing.dry_run_chunks(doc_id, guidance="")
    assert text == _text(spec, doc_id)
    _assert_spans_index_the_text(text, virtual)


async def test_a_fanned_out_document_is_rebased_at_finalize():
    # #227: a batch chunks its own units and knows only its own text; where
    # that text lands in the rejoined document is known at finalize, which is
    # where the offsets are rebased — before the #390 cache snapshots them.
    from workspace_app.kb.index_cache import IndexCacheStore

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    ing, _ = _ingestor(spec)
    coord = IndexCoordinator(spec, ing, wiki_coordinator=None, unit_batch_sizes={"CsvParser": 2})
    body = "name\n" + "".join(f"row{i}\n" for i in range(5))  # 5 rows / batch 2 → 3 batches
    (doc_id,) = ing.store(collection_id=cid, user="u", filename="people.csv", data=body.encode())
    coord.enqueue(doc_id, cid)
    await coord.aclose()
    chunks = _chunks(spec, doc_id)
    _assert_spans_index_the_text(_text(spec, doc_id), chunks)
    assert len(chunks) == 5 and len({c.start for c in chunks}) == 5
    cached = IndexCacheStore(spec).get(ing.cache_key(doc_id))
    assert cached is not None
    assert sorted((c.start, c.end) for c in cached.chunks) == sorted(
        (c.start, c.end) for c in chunks
    )


def _ctx(spec: SpecStar, emb: HashEmbedder, cid: str, **kw):
    return RunContextWrapper(
        AgentToolContext(
            spec=spec,
            retriever=Retriever(spec, embedder=emb, **kw),
            collection_ids=[cid],
            agent_config=AgentConfig(name="kb", model="x", vision=True),
        )
    )


async def test_grep_read_page_and_the_context_walk_see_page_two(spec: SpecStar):
    # The three consumers the plan built, on the page the old offsets hid.
    ing, emb = _ingestor(spec)
    cid = _collection(spec)
    ing.ingest(collection_id=cid, user="u", filename="paper.pdf", data=text_pdf(_PAGES))
    ctx = _ctx(spec, emb, cid)
    out = kb_grep_impl(ctx, "delta echo")
    assert "paper.pdf (p.2)" in out and "PAGE TWO line 1 delta echo" in out
    page = await read_page_impl(ctx, "paper.pdf", 2)
    assert isinstance(page, list) and isinstance(page[0], ToolOutputText)
    assert "PAGE TWO line 0 charlie" in page[0].text and "PAGE ONE" not in page[0].text
    # A page-3 hit widened by a few chars reaches into page 2 — its actual
    # neighbour — not into page 1 (where every page's span used to point).
    [hit] = Retriever(spec, embedder=emb, context_chars=5, top_k=1).search(
        "PAGE THREE line 0 foxtrot PAGE THREE line 1 golf", [cid]
    )
    assert hit.text.startswith("PAGE THREE")
    assert hit.context_text.endswith(hit.text) and "PAGE TWO" in hit.context_text
    assert "PAGE ONE" not in hit.context_text


def test_rebase_is_idempotent_by_construction():
    # A re-driven finalize (sweep after a crash) recomputes from `unit_start`,
    # so applying the rebase to already-rebased chunks lands on the same numbers.
    from workspace_app.kb.ingest import rebase_offsets

    def chunk(seq: int, start: int, end: int, unit_start: int | None) -> DocChunk:
        return DocChunk(
            collection_id="c", seq=seq, start=start, end=end, unit_start=unit_start, text="x"
        )

    stride = 1_000_000
    bases = {0: 0, 1: 100, 2: 250}
    fresh = [chunk(0, 0, 10, 0), chunk(stride, 0, 20, 0), chunk(stride + 1, 20, 30, 20)]
    once = rebase_offsets(fresh, lambda seq: bases.get(seq // stride))
    assert once == [(0, 0, 10), (1, 100, 120), (2, 120, 130)]
    rebased = [chunk(c.seq, s, e, c.unit_start) for c, (_, s, e) in zip(fresh, once, strict=True)]
    assert rebase_offsets(rebased, lambda seq: bases.get(seq // stride)) == once
    # No unit_start (single-job / legacy rows) or no base (a failed batch): untouched.
    assert rebase_offsets([chunk(0, 5, 9, None)], lambda _seq: 0) == []
    assert rebase_offsets([chunk(3 * stride, 0, 9, 0)], lambda seq: bases.get(seq // stride)) == []


def test_a_short_chunk_absorbed_as_overlap_keeps_the_next_chunk_in_place(spec: SpecStar):
    # Review round 4: `SentenceSplitter` closes a short sentence (≤ overlap
    # tokens) as its own chunk and then carries it WHOLE into the next chunk as
    # overlap — two consecutive pieces with the SAME start. P8's walk assumed
    # "the next piece starts after the previous one started", so the long
    # piece was not found from start+1 and got anchored on its head — which,
    # when that short sentence recurs later, is the later recurrence: a
    # 1.2k-char chunk pinned to 58 chars near the end, and kb_grep blind to
    # everything in it. P7 (LlamaIndex's own first-occurrence) had it right.
    unit = "the quick brown fox jumps over the lazy dog and keeps running through the field "
    big = (unit * 11).strip() + " " + " ".join(["word"] * 75) + "."
    body = "Note: see below. " + big + " Note: see below again, later in the document, and the end."
    ing, emb = _ingestor(spec)
    cid = _collection(spec)
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="notes.txt", data=body.encode())
    chunks = _chunks(spec, doc_id)
    _assert_spans_index_the_text(_text(spec, doc_id), chunks)
    # The shape this test is about really occurred: a piece that starts where
    # the previous one started (else the splitter changed and the test is moot).
    starts = [c.start for c in chunks]
    assert len(starts) != len(set(starts)), starts
    out = kb_grep_impl(_ctx(spec, emb, cid), "lazy dog")
    assert "notes.txt:1:" in out


def test_periodic_text_lands_on_the_cuts_not_one_period_apart(spec: SpecStar):
    # Round 4 (veracity): on the very document the "36 of 38 chunks in the
    # first 1.8k chars" number came from, P8 still crowded the chunks — the walk
    # from the previous START found the NEXT occurrence one period (~385 chars)
    # later, not the cut one chunk (~1k chars) later, so the context walk still
    # went 12k chars back. The next piece starts at or after the previous END
    # minus the overlap: the walk starts THERE.
    para = "The same paragraph appears again and again in this report, sentence after sentence. "
    body = para * 250  # ~21k chars, period 86 < a chunk (~1.1k): every chunk's text recurs
    ing, _ = _ingestor(spec)
    cid = _collection(spec)
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="rep.txt", data=body.encode())
    chunks = _chunks(spec, doc_id)
    text = _text(spec, doc_id)
    _assert_spans_index_the_text(text, chunks)
    assert len(chunks) >= 10
    # Consecutive chunks are a chunk apart (minus overlap), and the last one
    # reaches the end of the text — nothing is left uncovered by the crowding.
    gaps = [b.start - a.start for a, b in zip(chunks, chunks[1:], strict=False)]
    assert min(gaps) > len(para) * 5, gaps  # far more than one period
    assert chunks[-1].end >= len(text) - len(para)


@pytest.mark.parametrize(
    "para",
    [
        "The same paragraph appears again and again in this report, sentence after sentence. ",
        "The same paragraph appears again and again in this long report, sentence after sentence. ",
        "Row 17: temperature 21.5 C, humidity 40 percent, pressure 1013 hPa, wind 3 m/s. ",
        "這是一個重複出現的中文句子，用來測量位置偏移的大小，每次都一樣。",
    ],
)
def test_periodic_text_of_any_period_or_language_tiles_the_document(spec: SpecStar, para: str):
    # Round 5: P12's periodic test passed on a coincidence (16 tokens × 2 = the
    # 32-token overlap); one word more and the chunks drifted, compounding per
    # chunk; Chinese was untouched (a 256-token chunk of this sentence is 192
    # chars, not above the 192-char
    # bound, so the rule degenerated to "next occurrence"). Offsets now come
    # from the splitter, so the shape of the text cannot matter.
    body = para * 250
    ing, _ = _ingestor(spec)
    cid = _collection(spec)
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="rep.txt", data=body.encode())
    chunks = _chunks(spec, doc_id)
    text = _text(spec, doc_id)
    _assert_spans_index_the_text(text, chunks)
    assert len(chunks) >= 10
    gaps = [b.start - a.start for a, b in zip(chunks, chunks[1:], strict=False)]
    assert min(gaps) > len(para) * 3, gaps  # chunks apart, not periods apart
    assert chunks[-1].end == len(text)  # and the last one reaches the end


def test_the_unique_line_after_a_long_repetition_is_where_it_is(spec: SpecStar):
    # Round 5 (regression lens): after > 8 KB of drift, the one unique line —
    # the one a query hits — was anchored onto boilerplate 10 KB earlier.
    zh = "這是一個重複出現的中文句子，用來測量位置偏移的大小，每次都一樣。"
    body = zh * 400 + "最後這一句只出現一次，查詢會命中它。"
    ing, emb = _ingestor(spec)
    cid = _collection(spec)
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="zh.txt", data=body.encode())
    chunks = _chunks(spec, doc_id)
    text = _text(spec, doc_id)
    _assert_spans_index_the_text(text, chunks)
    last = chunks[-1]
    assert "只出現一次" in last.text and last.end == len(text)
    out = kb_grep_impl(_ctx(spec, emb, cid), "查詢會命中它")
    assert "zh.txt:1:" in out


def test_a_dot_leader_table_of_contents_is_fully_spanned(spec: SpecStar):
    # Round 5: the phrase fallback drops runs of dots, so a TOC line loses ~60
    # chars; P12's cover limit collapsed every chunk to its ~45-char head and
    # kb_grep went blind on the whole TOC. The span is the run of splits the
    # chunk was merged from — it covers the dropped leaders.
    toc = "\n".join(f"Section {i} title of the chapter {'.' * 60} {i * 3 + 1}" for i in range(60))
    ing, emb = _ingestor(spec)
    cid = _collection(spec)
    [doc_id] = ing.ingest(collection_id=cid, user="u", filename="toc.txt", data=toc.encode())
    chunks = _chunks(spec, doc_id)
    text = _text(spec, doc_id)
    assert len(chunks) >= 3
    for c in chunks:
        region = text[c.start : c.end]
        assert len(region) >= len(c.text) and region.startswith(c.text[:12]), (c.start, c.end)
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    for needle in ("Section 30 title", "Section 2 title", "Section 59 title"):
        assert "toc.txt:" in kb_grep_impl(_ctx(spec, emb, cid), needle), needle


def test_repeated_code_is_positioned_at_the_splitter_cuts():
    # CodeSplitter (this LlamaIndex) chunks by max_chars only — contiguous, no
    # overlap — so every chunk sits right after the previous one.
    from llama_index.core.schema import Document

    from workspace_app.kb.li_pipeline import DispatchSplitter

    py = "def f(x):\n    return x + 1\n\n" * 400
    nodes = DispatchSplitter()(
        [Document(text=py, metadata={"filename": "r.py", "mime": "text/x-python"})]
    )
    cur = 0
    truth = []
    for n in nodes:
        body = n.get_content().split("\n\n", 1)[1]
        pos = py.find(body, cur)
        truth.append((pos, pos + len(body)))
        cur = pos + len(body)
    assert [(n.start_char_idx, n.end_char_idx) for n in nodes] == truth
