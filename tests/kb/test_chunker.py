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
