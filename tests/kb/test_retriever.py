from collections.abc import Iterator

from specstar import SpecStar

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
from workspace_app.resources.kb import Collection


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
        def collect(self, prompt: str, *, on_chunk=None) -> str:  # type: ignore[override]
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
