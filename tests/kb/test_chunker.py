from workspace_app.kb.chunker import Chunk, FixedTokenChunker


def test_windows_text_with_overlap_and_verbatim_spans():
    chunks = FixedTokenChunker(max_tokens=3, overlap_tokens=1).chunk("a b c d e")
    # step = 3-1 = 2 → windows [a b c], [c d e] (overlap on "c")
    assert [c.text for c in chunks] == ["a b c", "c d e"]
    assert [c.seq for c in chunks] == [0, 1]
    # spans are verbatim slices of the source text
    src = "a b c d e"
    assert all(src[c.start : c.end] == c.text for c in chunks)
    assert chunks[0].start == 0 and chunks[0].end == 5
    assert chunks[1].start == 4 and chunks[1].end == 9  # overlaps chunk 0
    assert isinstance(chunks[0], Chunk)


def test_blank_text_yields_no_chunks():
    assert FixedTokenChunker().chunk("") == []
    assert FixedTokenChunker().chunk("   \n\t ") == []


def test_text_shorter_than_window_is_one_chunk_spanning_all():
    chunks = FixedTokenChunker(max_tokens=10, overlap_tokens=2).chunk("just a few words")
    assert len(chunks) == 1
    assert chunks[0].seq == 0
    assert chunks[0].text == "just a few words"
    assert chunks[0].start == 0 and chunks[0].end == len("just a few words")


def test_cjk_characters_window_like_words():
    # Five ideographs with no whitespace must window exactly as "a b c d e"
    # does: each CJK character is one token. Under the old `\S+` tokenizer the
    # whole string was ONE token and came back as one chunk — a 12k-char
    # Chinese document then became a single embedding (plan-rag-context P1).
    src = "甲乙丙丁戊"
    chunks = FixedTokenChunker(max_tokens=3, overlap_tokens=1).chunk(src)
    assert [c.text for c in chunks] == ["甲乙丙", "丙丁戊"]
    assert [c.seq for c in chunks] == [0, 1]
    assert all(src[c.start : c.end] == c.text for c in chunks)
    assert chunks[0].start == 0 and chunks[0].end == 3
    assert chunks[1].start == 2 and chunks[1].end == 5  # overlaps chunk 0 on 丙


def test_mixed_cjk_and_latin_tokenizes_each_script_its_own_way():
    # "用A方法" is four tokens: 用 | A | 方 | 法 — a Latin run stops at the next
    # CJK character instead of swallowing it. Windows of 2 with no overlap
    # therefore split between "A" and "方".
    src = "用A方法"
    chunks = FixedTokenChunker(max_tokens=2, overlap_tokens=0).chunk(src)
    assert [c.text for c in chunks] == ["用A", "方法"]
    assert all(src[c.start : c.end] == c.text for c in chunks)


def test_long_cjk_text_splits_at_default_settings():
    # The reported symptom: at the defaults (256 / 32) a run of ideographs used
    # to be ONE chunk however long. 1,000 characters → step 224 → windows at
    # 0, 224, 448, 672, 896 — five chunks of ≤ 256 chars, each overlapping the
    # previous by 32.
    src = "文" * 1000
    chunks = FixedTokenChunker().chunk(src)
    assert len(chunks) == 5
    assert [c.start for c in chunks] == [0, 224, 448, 672, 896]
    assert all(c.end - c.start <= 256 for c in chunks)
    assert all(a.end - b.start == 32 for a, b in zip(chunks, chunks[1:], strict=False))
