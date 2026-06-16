from collections.abc import Iterator

from agents import RunContextWrapper
from specstar import SpecStar

from workspace_app.agent import AgentToolContext, kb_search_impl
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.llm import ILlm
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection


class _FakeLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield "gamma", False


def _kb_ctx(spec: SpecStar, embedder: HashEmbedder, collection_ids: list[str]):
    return RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=embedder),
            collection_ids=collection_ids,
        )
    )


async def test_kb_search_returns_numbered_passages_and_fills_registry(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ctx = _kb_ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "reflow temperature")

    assert "[1]" in out  # numbered for the LLM to cite
    assert "reflow" in out
    # the passage is recorded so a later [n] in the answer maps back to a Citation
    assert len(ctx.context.kb_passages) == 1
    assert ctx.context.kb_passages[0].document_id == encode_doc_id(cid, "reflow.md")


async def test_kb_search_on_empty_returns_no_results_message(
    spec: SpecStar, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="empty")).resource_id
    ctx = _kb_ctx(spec, embedder, [cid])

    out = kb_search_impl(ctx, "anything")

    assert "no" in out.lower()  # tells the agent nothing matched
    assert ctx.context.kb_passages == []


async def test_kb_search_streams_enhancement_thinking_to_the_run_sink(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # When the run wired an output sink, the retriever's enhancement-LLM work is
    # streamed to it (so it shows live under the kb_search tool card) — issue #10.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    captured: list[bytes] = []
    ctx = RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=embedder, llm=_FakeLlm()),
            collection_ids=[cid],
            on_exec_output=captured.append,
        )
    )

    kb_search_impl(ctx, "gamma")

    text = b"".join(captured).decode()
    assert "↻ expanding query" in text  # step label streamed to the sink
    assert "gamma" in text  # the model's streamed chunk


async def test_kb_search_keeps_numbers_stable_across_calls(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # the agentic case: the agent searches again and re-finds the same passage —
    # its citation number must not change, and it must not be double-registered.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ctx = _kb_ctx(spec, embedder, [cid])

    first = kb_search_impl(ctx, "reflow temperature")
    second = kb_search_impl(ctx, "temperature reflow")  # re-finds the same passage

    assert "[1]" in first and "[1]" in second  # same passage, same number
    assert len(ctx.context.kb_passages) == 1  # not double-registered
