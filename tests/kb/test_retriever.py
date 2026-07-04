from collections.abc import Iterator

import msgspec
from specstar import SpecStar
from specstar.types import Binary

from workspace_app.config.schema import (
    EnhancementBool,
    EnhancementInt,
    EnhancementSettings,
)
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.llm import ILlm
from workspace_app.kb.retriever import Enhancements, Retriever
from workspace_app.resources.kb import Collection, SourceDoc


def _ingest(spec, chunker, embedder, name, text):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename=name, data=text.encode()
    )
    return cid


def test_hybrid_search_surfaces_the_keyword_matching_document(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # one collection with two docs; the query terms only match doc A
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="cats.md",
        data=b"the cat sat quietly on the warm mat all afternoon",
    )

    passages = Retriever(spec, embedder=embedder).search("reflow temperature", [cid])
    assert passages, "expected at least one passage"
    # keyword-matching doc on top
    assert passages[0].document_id == encode_doc_id(cid, "reflow.md")
    assert "reflow" in passages[0].text


def test_depth_returns_ranks_beyond_top_k(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#328: search(depth=N) widens the internal candidate / MMR caps and returns
    the full ranked passage list (up to N) instead of the top_k slice — so the
    findability probe can see where a doc's chunk lands beyond the 5 a user
    normally sees. depth=None is byte-for-byte the current behaviour."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    for i in range(8):
        ing.ingest(
            collection_id=cid,
            user="u",
            filename=f"d{i}.md",
            data=f"solder void analysis report number {i}".encode(),
        )
    r = Retriever(spec, embedder=embedder)  # top_k defaults to 5
    shallow = r.search("solder void", [cid])
    deep = r.search("solder void", [cid], depth=8)
    assert len(shallow) == 5  # the user-facing top_k slice
    assert len(deep) == 8  # every matching doc ranked, beyond the top 5
    # depth=None inherits the default slice exactly.
    assert [p.document_id for p in r.search("solder void", [cid], depth=None)] == [
        p.document_id for p in shallow
    ]


def test_overlay_swaps_a_docs_chunks_for_virtual_ones(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#328: search(overlay=...) ranks as if the shadowed doc held the supplied
    virtual chunks instead of its real ones — the virtual chunk competes through
    the SAME hybrid pipeline (so a dry-run prompt preview needs no reindex), and
    the shadowed doc's real chunks drop out of the candidate set."""
    from workspace_app.kb.retriever import Overlay
    from workspace_app.resources.kb import DocChunk

    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="a.md",
        data=b"reflow oven temperature drifted in zone three",
    )
    ing.ingest(collection_id=cid, user="u", filename="b.md", data=b"unrelated cat nap content")
    a_id = encode_doc_id(cid, "a.md")

    vtext = "hydraulic actuator pressure loss"
    virtual = DocChunk(
        collection_id=cid,
        source_doc_id=a_id,
        seq=0,
        start=0,
        end=len(vtext),
        text=vtext,
        embedding=embedder.embed_documents([vtext])[0],
    )
    overlay = Overlay(virtual_chunks=[virtual], shadow_doc_id=a_id, virtual_text=vtext)
    r = Retriever(spec, embedder=embedder)

    # the virtual chunk flows through the real pipeline and is retrievable
    found = r.search(vtext, [cid], overlay=overlay)
    assert any("hydraulic" in p.text for p in found)
    # the shadowed doc's REAL chunk no longer competes
    on_old = r.search("reflow temperature", [cid], overlay=overlay)
    assert not any("reflow" in p.text for p in on_old)


def test_image_doc_passage_uses_parsed_text_not_raw_bytes(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#114: an image SourceDoc keeps its raw (non-UTF-8) bytes on `content`
    but the chunk offsets index into the parser's extracted `text`. The
    retriever must slice that stored text, never `content.decode(...)` — else
    the LLM gets U+FFFD image-byte garbage instead of the parsed markdown."""
    cid = _ingest(spec, chunker, embedder, "diagram.png", "alpha beta gamma delta epsilon")
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "diagram.png")
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    # swap the stored bytes for real image bytes (invalid UTF-8); `text` stays
    png = b"\x89PNG\r\n\x1a\n\xff\xd8\xff\xe0\x00\x10garbage\x80\x81\x82"
    rm.update(
        doc_id,
        msgspec.structs.replace(doc, content=Binary(data=png, content_type="image/png")),
    )

    passages = Retriever(spec, embedder=embedder).search("alpha", [cid])
    assert passages, "expected a passage for the image doc"
    assert "�" not in passages[0].text
    assert "alpha" in passages[0].text


def test_legacy_doc_without_stored_text_decodes_clean_utf8_bytes(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """A row predating stored `text` (text=None) but holding clean UTF-8 bytes
    still resolves its passage by decoding `content` — plain-text uploads keep
    working without a reindex."""
    cid = _ingest(spec, chunker, embedder, "legacy.md", "alpha beta gamma delta epsilon")
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "legacy.md")
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    rm.update(doc_id, msgspec.structs.replace(doc, text=None))

    passages = Retriever(spec, embedder=embedder).search("alpha", [cid])
    assert passages
    assert "alpha" in passages[0].text


def test_legacy_binary_doc_without_text_shows_marker_not_byte_garbage(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """A legacy binary row with no extracted text (text=None, non-UTF-8 bytes)
    surfaces a readable marker, never U+FFFD replacement-char garbage (#114)."""
    cid = _ingest(spec, chunker, embedder, "old.png", "alpha beta gamma delta epsilon")
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "old.png")
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    png = b"\x89PNG\r\n\x1a\n\xff\xd8\xff\xe0\x00\x10garbage\x80\x81\x82"
    rm.update(
        doc_id,
        msgspec.structs.replace(doc, text=None, content=Binary(data=png, content_type="image/png")),
    )

    passages = Retriever(spec, embedder=embedder).search("alpha", [cid])
    assert passages
    assert "�" not in passages[0].text


def test_search_over_empty_collection_returns_nothing(spec: SpecStar, embedder: HashEmbedder):
    cid = spec.get_resource_manager(Collection).create(Collection(name="empty")).resource_id
    assert Retriever(spec, embedder=embedder).search("anything", [cid]) == []


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._reply, False


def test_multiquery_widens_recall_via_llm_variants(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon zeta"
    )
    # the query itself matches nothing; the LLM variant "gamma" does
    fake = _FakeLlm("gamma")
    passages = Retriever(spec, embedder=embedder, llm=fake).search("zzz nomatch", [cid])
    assert fake.prompts  # the multi-query step consulted the LLM
    # surfaced via the variant
    assert any(p.document_id == encode_doc_id(cid, "g.md") for p in passages)


def test_search_streams_enhancement_llm_thinking_via_on_progress(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """With all three knobs explicitly raised, every enhancement step
    labels itself through `on_progress` and its (fake) LLM output
    streams. Confirms the wiring; bundled defaults set HyDE off, so
    we raise it here to exercise all three paths together."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma",
        [cid],
        on_progress=lambda t, r: events.append((t, r)),
        enhancements=Enhancements(expand=1, hyde=1, rerank=True),
    )
    text = "".join(t for t, _ in events)
    # each enhancement step is labelled and its LLM output is streamed through
    assert "↻ expanding query" in text
    assert "↻ HyDE" in text
    assert "↻ rerank" in text
    assert "gamma" in text  # the (fake) model's streamed chunk


def test_search_caller_can_skip_all_enhancements_per_call(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """Passing `Enhancements(expand=0, hyde=0, rerank=False)` is the
    explicit "skip everything" path — the dense + BM25 fusion still
    runs. Replaces the legacy `quick=True` knob."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma",
        [cid],
        on_progress=lambda t, r: events.append((t, r)),
        enhancements=Enhancements(expand=0, hyde=0, rerank=False),
    )
    text = "".join(t for t, _ in events)
    assert "↻ expanding query" not in text
    assert "↻ HyDE" not in text
    assert "↻ rerank" not in text


def test_search_default_uses_shipped_enhancements_light(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """Bundled `EnhancementSettings()` is intentionally light: expand=1,
    hyde=0, rerank=on. So a default Retriever() runs expand + rerank
    but NOT HyDE — operators raise the knob explicitly when they want
    HyDE."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma", [cid], on_progress=lambda t, r: events.append((t, r))
    )
    text = "".join(t for t, _ in events)
    assert "↻ expanding query" in text
    assert "↻ HyDE" not in text  # bundled hyde.default == 0
    assert "↻ rerank" in text


def test_search_operator_max_clamps_caller_values(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """An over-eager caller asking for `expand=99` is clamped to the
    operator's `expand.max` (here `2`). Same shape for hyde / rerank."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    captured: list[int] = []

    class _RecordingLlm(_FakeLlm):
        def collect(self, prompt: str, *, on_chunk=None) -> str:  # ty: ignore
            if "alternative phrasings" in prompt:
                # `expand_queries(n=X)` weaves N into the prompt — read it
                # back so the test asserts the threaded value, not just
                # the labels.
                for token in prompt.split():
                    if token.isdigit():
                        captured.append(int(token))
                        break
            return super().collect(prompt, on_chunk=on_chunk)

    Retriever(
        spec,
        embedder=embedder,
        llm=_RecordingLlm("gamma"),
        enhancement_defaults=EnhancementSettings(
            expand=EnhancementInt(default=1, max=2),
            hyde=EnhancementInt(default=0, max=0),
            rerank=EnhancementBool(default=False, max=False),
        ),
    ).search(
        "gamma",
        [cid],
        enhancements=Enhancements(expand=99, hyde=99, rerank=True),
    )
    assert captured == [2]


def test_search_resolution_picks_caller_over_operator_default(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """Caller-set values override the operator default (so long as
    they don't exceed `max`). The bundled default has hyde=0; caller
    asking for hyde=1 (under max) flips it on."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma",
        [cid],
        on_progress=lambda t, r: events.append((t, r)),
        enhancements=Enhancements(hyde=1),  # raise above default 0
    )
    text = "".join(t for t, _ in events)
    assert "↻ HyDE" in text


def test_empty_llm_replies_fall_back_to_the_plain_query(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # an LLM that returns nothing: no extra phrasings, no HyDE doc — still works
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="reflow.md", data=b"reflow oven temperature drift"
    )
    passages = Retriever(spec, embedder=embedder, llm=_FakeLlm("   ")).search("reflow", [cid])
    assert passages[0].document_id == encode_doc_id(cid, "reflow.md")


def test_search_excludes_denied_docs(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#308: `exclude_doc_ids` drops a doc's chunks from BOTH the dense and the
    BM25 paths, so a doc the speaker's per-doc override blocks never reaches
    ranking or the answer."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="second.md",
        data=b"reflow temperature also matters greatly for this second document here",
    )
    blocked = encode_doc_id(cid, "reflow.md")
    r = Retriever(spec, embedder=embedder)
    # baseline: the reflow doc is retrievable
    assert any(p.document_id == blocked for p in r.search("reflow temperature", [cid]))
    # excluding it removes every passage from that doc; the other doc still returns
    got = r.search("reflow temperature", [cid], exclude_doc_ids=frozenset({blocked}))
    assert got, "the non-excluded doc should still return passages"
    assert all(p.document_id != blocked for p in got)
