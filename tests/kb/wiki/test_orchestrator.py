"""WikiAwareRunner (#50 P5) — route a KB chat turn across chunk-RAG / wiki /
both, merging when both apply, and citing into one shared source list.

A fake base runner dispatches on the sub-context to emulate the three sub-runs
(chunk search, wiki reader, merge), so the test proves the routing + citation
renumbering without a real LLM.
"""

from __future__ import annotations

from agents import RunContextWrapper
from specstar.types import Binary

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import read_source_impl
from workspace_app.api.events import MessageDelta, RunError
from workspace_app.kb.citations import parse_citations
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.wiki.orchestrator import WikiAwareRunner
from workspace_app.resources import AgentConfig, Collection, SourceDoc, make_spec
from workspace_app.resources.kb import RetrievedPassage


def _add_source(spec, cid, path, text):
    # The natural-key id (as the Ingestor mints it) so path → doc resolves by id.
    rm = spec.get_resource_manager(SourceDoc)
    return rm.create(
        SourceDoc(collection_id=cid, path=path, content=Binary(data=text.encode()), text=text),
        resource_id=encode_doc_id(cid, path),
    ).resource_id


class _FakeBase:
    """Emulates chunk / wiki / merge sub-runs by inspecting the context."""

    async def run(self, prompt, ctx):
        cfg = ctx.agent_config
        if cfg is not None and cfg.name == "Wiki Merge":
            cites = " ".join(f"[{i + 1}]" for i in range(len(ctx.kb_passages)))
            yield MessageDelta(text=f"Merged answer {cites}.")
            return
        if ctx.wiki_sources is not None and ctx.wiki_cite_sources:
            await read_source_impl(RunContextWrapper(ctx), "spec.md")  # registers [1]
            yield MessageDelta(text="Wiki says zone 3 is 245C [1].")
            return
        # chunk path: register a passage and cite it
        ctx.kb_passages.append(
            RetrievedPassage(
                collection_id="c-rag",
                document_id="chunk-doc",
                filename="data.md",
                start=0,
                end=5,
                source_chunk_ids=["ch1"],
                text="chunk fact",
                score=1.0,
            )
        )
        yield MessageDelta(text="Docs say zone 3 runs hot [1].")


class _ConfigCapture:
    """Like _FakeBase, but records the system prompt of each sub-run's config so
    a test can assert WHERE the per-collection reader guidance is applied."""

    def __init__(self) -> None:
        self.reader_sys: str | None = None
        self.merge_sys: str | None = None
        self.chunk_sys: str | None = None

    async def run(self, prompt, ctx):
        cfg = ctx.agent_config
        if cfg is not None and cfg.name == "Wiki Merge":
            self.merge_sys = cfg.system_prompt
            yield MessageDelta(text="Merged answer.")
            return
        if ctx.wiki_sources is not None and ctx.wiki_cite_sources:
            self.reader_sys = cfg.system_prompt
            await read_source_impl(RunContextWrapper(ctx), "spec.md")  # registers [1]
            yield MessageDelta(text="Wiki says zone 3 is 245C [1].")
            return
        self.chunk_sys = cfg.system_prompt if cfg is not None else None
        ctx.kb_passages.append(
            RetrievedPassage(
                collection_id="c-rag",
                document_id="chunk-doc",
                filename="data.md",
                start=0,
                end=5,
                source_chunk_ids=["ch1"],
                text="chunk fact",
                score=1.0,
            )
        )
        yield MessageDelta(text="Docs say zone 3 runs hot [1].")


async def test_wiki_reader_gets_the_collection_reader_guidance():
    """#90: the wiki reader sub-run is driven with the bundled reader prompt PLUS
    the collection's own reader guidance appended."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(
            Collection(
                name="c",
                use_rag=False,
                use_wiki=True,
                wiki_reader_guidance="Answer with a TL;DR first.",
            )
        )
        .resource_id
    )
    _add_source(spec, cid, "spec.md", "Zone 3 setpoint 245C.")
    base = _ConfigCapture()
    runner = WikiAwareRunner(base, spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=True
    )

    await _drive(runner, "q", ctx)
    assert base.reader_sys is not None
    assert "Answer with a TL;DR first." in base.reader_sys
    assert "## Collection-specific guidance" in base.reader_sys


async def test_reader_guidance_does_not_leak_into_the_chunk_or_merge_agents():
    """#90 scoping: reader guidance shapes only THIS wiki's reader draft. The
    chunk-RAG agent and the cross-collection merge agent must never receive it
    (merge spans collections — there is no single owner)."""
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(
            Collection(
                name="c",
                use_rag=True,
                use_wiki=True,
                wiki_reader_guidance="Answer with a TL;DR first.",
            )
        )
        .resource_id
    )
    _add_source(spec, cid, "spec.md", "Zone 3 setpoint 245C.")
    base = _ConfigCapture()
    runner = WikiAwareRunner(base, spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=True
    )

    answer = await _drive(runner, "q", ctx)
    assert answer.startswith("Merged answer")  # both drafts → merge ran
    # the reader got the guidance…
    assert base.reader_sys is not None and "## Collection-specific guidance" in base.reader_sys
    # …but the chunk and merge agents did NOT
    assert base.chunk_sys is not None and "## Collection-specific guidance" not in base.chunk_sys
    assert base.merge_sys is not None and "## Collection-specific guidance" not in base.merge_sys


async def _drive(runner, prompt, ctx) -> str:
    parts = []
    async for ev in runner.run(prompt, ctx):
        if isinstance(ev, MessageDelta):
            parts.append(ev.text)
    return "".join(parts)


async def test_wiki_off_is_a_pure_chunk_rag_passthrough():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=True, use_wiki=True))
        .resource_id
    )
    runner = WikiAwareRunner(_FakeBase(), spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=False
    )

    answer = await _drive(runner, "q", ctx)
    assert answer == "Docs say zone 3 runs hot [1]."
    assert len(parse_citations(answer, ctx.kb_passages)) == 1


async def test_wiki_opted_in_but_no_wiki_collection_is_passthrough():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=True, use_wiki=False))
        .resource_id
    )
    runner = WikiAwareRunner(_FakeBase(), spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=True
    )
    answer = await _drive(runner, "q", ctx)
    assert answer == "Docs say zone 3 runs hot [1]."


async def test_wiki_only_streams_the_reader_and_cites_the_source():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=False, use_wiki=True))
        .resource_id
    )
    src = _add_source(spec, cid, "spec.md", "Zone 3 setpoint 245C.")
    runner = WikiAwareRunner(_FakeBase(), spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=True
    )

    answer = await _drive(runner, "q", ctx)
    assert "245C" in answer
    cites = parse_citations(answer, ctx.kb_passages)
    assert len(cites) == 1
    assert cites[0].document_id == src  # cites the underlying SourceDoc


async def test_both_runs_two_agents_and_merges_into_one_shared_citation_list():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=True, use_wiki=True))
        .resource_id
    )
    src = _add_source(spec, cid, "spec.md", "Zone 3 setpoint 245C.")
    runner = WikiAwareRunner(_FakeBase(), spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=True
    )

    answer = await _drive(runner, "q", ctx)
    # The merge agent ran (not a raw draft) and cited the shared list.
    assert answer.startswith("Merged answer")
    cites = parse_citations(answer, ctx.kb_passages)
    assert len(cites) == 2  # chunk passage + wiki source, renumbered [1],[2]
    docs = {c.document_id for c in cites}
    assert "chunk-doc" in docs and src in docs


class _ChunkOkWikiErrors:
    """The chunk agent answers; the wiki sub-agent errors out."""

    async def run(self, prompt, ctx):
        if ctx.wiki_sources is not None and ctx.wiki_cite_sources:
            yield RunError(message="wiki down")
            return
        ctx.kb_passages.append(
            RetrievedPassage(
                collection_id="c-rag",
                document_id="chunk-doc",
                filename="data.md",
                start=0,
                end=5,
                source_chunk_ids=["ch1"],
                text="chunk fact",
                score=1.0,
            )
        )
        yield MessageDelta(text="Docs say zone 3 runs hot [1].")


async def test_a_failing_source_is_dropped_and_the_survivor_streams_without_merge():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=True, use_wiki=True))
        .resource_id
    )
    _add_source(spec, cid, "spec.md", "x")
    runner = WikiAwareRunner(_ChunkOkWikiErrors(), spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=True
    )

    answer = await _drive(runner, "q", ctx)
    # Only the chunk draft survived → streamed as-is, no merge wrapper.
    assert answer == "Docs say zone 3 runs hot [1]."
    assert len(parse_citations(answer, ctx.kb_passages)) == 1


class _EverythingEmpty:
    async def run(self, prompt, ctx):
        if False:
            yield  # pragma: no cover — empty async generator (no answer)


async def test_when_no_source_finds_anything_a_fallback_is_streamed():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=True, use_wiki=True))
        .resource_id
    )
    runner = WikiAwareRunner(_EverythingEmpty(), spec)
    ctx = AgentToolContext(
        collection_ids=[cid], agent_config=AgentConfig(name="KB"), wiki_query=True
    )
    answer = await _drive(runner, "q", ctx)
    assert "couldn't find anything" in answer


async def test_two_wiki_collections_merge_and_unknown_ids_are_skipped():
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(Collection)
    c1 = rm.create(Collection(name="a", use_rag=False, use_wiki=True)).resource_id
    c2 = rm.create(Collection(name="b", use_rag=False, use_wiki=True)).resource_id
    _add_source(spec, c1, "spec.md", "fact one")
    _add_source(spec, c2, "spec.md", "fact two")
    runner = WikiAwareRunner(_FakeBase(), spec)
    # A bogus collection id is mixed in — it must be skipped, not crash.
    ctx = AgentToolContext(
        collection_ids=[c1, "ghost", c2], agent_config=AgentConfig(name="KB"), wiki_query=True
    )

    answer = await _drive(runner, "q", ctx)
    assert answer.startswith("Merged answer")  # 2 wiki drafts → merged
    assert len(parse_citations(answer, ctx.kb_passages)) == 2
