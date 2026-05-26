from collections.abc import Iterator

from specstar import SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.llm import ILlm
from workspace_app.kb.retriever import Retriever
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
    assert passages[0].document_id == encode_doc_id(cid, "u", "reflow.md")
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
    assert any(p.document_id == encode_doc_id(cid, "u", "g.md") for p in passages)


def test_search_streams_enhancement_llm_thinking_via_on_progress(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma", [cid], on_progress=lambda t, r: events.append((t, r))
    )
    text = "".join(t for t, _ in events)
    # each enhancement step is labelled and its LLM output is streamed through
    assert "↻ expanding query" in text
    assert "↻ HyDE" in text
    assert "↻ rerank" in text
    assert "gamma" in text  # the (fake) model's streamed chunk


def test_empty_llm_replies_fall_back_to_the_plain_query(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # an LLM that returns nothing: no extra phrasings, no HyDE doc — still works
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="reflow.md", data=b"reflow oven temperature drift"
    )
    passages = Retriever(spec, embedder=embedder, llm=_FakeLlm("   ")).search("reflow", [cid])
    assert passages[0].document_id == encode_doc_id(cid, "u", "reflow.md")
