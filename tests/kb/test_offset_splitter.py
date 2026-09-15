"""plan-rag-context P14 — chunk offsets come from the splitter, not from a search.

Rounds 3–5 showed that locating a chunk's text in the document cannot be made
exact: the search floor has to approximate an overlap the splitter computes as
a token sum over whole splits, and every char bound was either inert (CJK,
where a 256-token chunk of the test's sentence is 192 chars, under the 192-char
bound) or a period too loose (English sentences one
word longer than the test's), with the error compounding per chunk. The
splitter knows where it cut. `OffsetSentenceSplitter` carries the char offset
of every split through LlamaIndex's own `_split`/`_merge` and reconstructs each
chunk's span from the run of splits it was merged from — exact for verbatim
chunks AND for the ones the phrase fallback rewrote — 4.6% of Markdown prose
windows, 0.9% of all sentence-split chunks (their span covers the
dropped punctuation)."""

from __future__ import annotations

import pytest
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document

from workspace_app.kb.li_pipeline import OffsetSentenceSplitter

_ZH = "這是一個重複出現的中文句子，用來測量位置偏移的大小，每次都一樣。"
_TOC = "\n".join(f"Section {i} title of the chapter {'.' * 60} {i * 3 + 1}" for i in range(60))
_P16 = "The same paragraph appears again and again in this report, sentence after sentence. "
_P17 = "The same paragraph appears again and again in this long report, sentence after sentence. "
_ROW = "Row 17: temperature 21.5 C, humidity 40 percent, pressure 1013 hPa, wind 3 m/s. "
_SHAPES = {
    "periodic-16tok": _P16 * 250,
    "periodic-17tok": _P17 * 250,
    "periodic-row": _ROW * 250,
    "periodic-zh": _ZH * 400,
    "zh-then-unique": _ZH * 400 + "最後這一句只出現一次，查詢會命中它。",
    "log-then-unique": "2026-09-14 12:00:00 worker: heartbeat ok, queue depth 0, nothing to do\n"
    * 800
    + "2026-09-14 12:13:37 worker: OutOfMemory in batch 41, retrying with half the rows\n",
    "absorbed-recur": (
        "Note: see below. "
        + ("the quick brown fox jumps over the lazy dog and keeps running " * 40).strip()
        + ". "
    )
    * 6,
    "toc-dot-leaders": _TOC,
    "punct-runs": ("exec(...) then wait!!! and again?!?! " * 80),
    "lorem-short-period": "Lorem ipsum dolor sit amet. " * 400,
    "prose": " ".join(
        f"Sentence number {i} says something slightly different about item {i % 7}."
        for i in range(300)
    ),
    "leading-ws": "\n\n   " + " ".join(f"word{i}" for i in range(2000)) + "\n\n",
    "empty": "",
}


@pytest.mark.parametrize("name", sorted(_SHAPES))
def test_same_chunks_as_the_stock_splitter_with_exact_spans(name: str):
    text = _SHAPES[name]
    stock = SentenceSplitter(chunk_size=256, chunk_overlap=32)
    ours = OffsetSentenceSplitter(chunk_size=256, chunk_overlap=32)
    assert ours.split_text(text) == stock.split_text(text)  # the chunks are LlamaIndex's
    nodes = ours.get_nodes_from_documents([Document(text=text)])
    assert [n.get_content() for n in nodes] == stock.split_text(text)
    prev_start, prev_end = 0, 0
    for n in nodes:
        s, e = n.start_char_idx, n.end_char_idx
        assert s is not None and e is not None
        piece, region = n.get_content(), text[s:e]
        if region == piece:
            pass  # verbatim
        else:
            # the phrase fallback dropped consecutive punctuation: the span is the
            # run of splits, so it holds the piece as a subsequence with the same head
            assert region.startswith(piece[:8]) and _is_subsequence(piece, region), (name, s, e)
        # never before where the previous one started
        assert prev_start <= s, (name, s, prev_start, prev_end)
        prev_start, prev_end = s, e
    if text.strip():
        assert nodes[0].start_char_idx == len(text) - len(text.lstrip())
        assert nodes[-1].end_char_idx == len(text.rstrip())
        # …and the runs COVER the text: every character outside every span is
        # whitespace or punctuation the phrase fallback drops between runs.
        # This catches a gap of at least the overlap (a chunk missing, a run
        # mis-sized); a drift smaller than the overlap hides inside it, and
        # for that the guard is structural — `_spans_of_runs` raises unless
        # the run's splits concatenate to the raw chunk exactly (round 9).
        covered = bytearray(len(text))
        for n in nodes:
            for i in range(n.start_char_idx, n.end_char_idx):
                covered[i] = 1
        gaps = {text[i] for i in range(len(text)) if not covered[i]}
        assert gaps <= set(" \t\n\r,.;。？！"), (name, sorted(gaps))


def _is_subsequence(needle: str, hay: str) -> bool:
    i = 0
    for ch in hay:
        if i < len(needle) and ch == needle[i]:
            i += 1
    return i == len(needle)


def test_metadata_aware_chunk_size_is_honoured_like_the_stock_splitter():
    # The stock splitter shrinks the chunk budget by the node's metadata length;
    # the chunks — and therefore the spans — must be computed at that same size.
    text = " ".join(f"Sentence number {i} says something about item {i % 7}." for i in range(300))
    meta = {
        "filename": "x.md",
        "mime": "text/markdown",
        "title": "A title long enough to matter " * 4,
    }
    stock = SentenceSplitter(chunk_size=256, chunk_overlap=32).get_nodes_from_documents(
        [Document(text=text, metadata=meta)]
    )
    ours = OffsetSentenceSplitter(chunk_size=256, chunk_overlap=32).get_nodes_from_documents(
        [Document(text=text, metadata=meta)]
    )
    assert [n.get_content() for n in ours] == [n.get_content() for n in stock]
    assert all(text[n.start_char_idx : n.end_char_idx] == n.get_content() for n in ours)


def test_a_changed_merge_fails_loudly_not_silently():
    # The span reconstruction rests on "a chunk is a run of consecutive splits";
    # if a LlamaIndex upgrade breaks that, the ingest must error, not stamp
    # plausible-looking offsets.
    from workspace_app.kb.li_pipeline import _OffsetSplit, _spans_of_runs

    splits = [_OffsetSplit("ab ", True, 1, 0), _OffsetSplit("cd", True, 1, 3)]
    assert _spans_of_runs(splits, ["ab cd"], chunk_overlap=32) == [(0, 5)]
    with pytest.raises(RuntimeError, match="LlamaIndex"):
        _spans_of_runs(splits, ["ab xx"], chunk_overlap=32)


def test_the_per_call_state_survives_copy_and_serialisation():
    # Round 6/9: the thread-local was a plain `__dict__` entry on the pydantic
    # model — `to_json()` raised, and the base component's `__getstate__`
    # stripped it from the LIVE instance on copy / pickle. A private attribute
    # is invisible to both.
    import copy
    import pickle

    text = " ".join(f"Sentence number {i} about item {i % 7}." for i in range(300))
    sp = OffsetSentenceSplitter(chunk_size=256, chunk_overlap=32)
    before = [n.start_char_idx for n in sp.get_nodes_from_documents([Document(text=text)])]
    sp.to_json()  # must not raise
    twin = copy.deepcopy(sp)
    pickle.loads(pickle.dumps(sp))
    # the original still works after being copied / pickled, and so does the copy
    after = [n.start_char_idx for n in sp.get_nodes_from_documents([Document(text=text)])]
    twins = [n.start_char_idx for n in twin.get_nodes_from_documents([Document(text=text)])]
    assert after == before == twins


def test_spans_of_runs_skip_a_blank_chunk_but_carry_its_overlap():
    from workspace_app.kb.li_pipeline import _OffsetSplit, _spans_of_runs

    splits = [
        _OffsetSplit("x ", True, 1, 0),
        _OffsetSplit("  ", True, 1, 2),
        _OffsetSplit("y", True, 1, 4),
    ]
    # The middle raw chunk is whitespace only: no span for it, but the next
    # chunk's run still starts where `_merge`'s overlap rule says.
    assert _spans_of_runs(splits, ["x ", "  ", "y"], chunk_overlap=0) == [(0, 1), (4, 5)]


def test_place_after_leaves_a_node_it_cannot_find_where_it_was():
    from llama_index.core.schema import TextNode

    from workspace_app.kb.li_pipeline import _place_after

    found, empty = TextNode(text="beta"), TextNode(text="")
    _place_after("alpha beta", [empty, found])
    assert (empty.start_char_idx, empty.end_char_idx) == (None, None)
    assert (found.start_char_idx, found.end_char_idx) == (6, 10)
