"""answer_question(wiki=True) routes the RCA→KB bridge through the wiki path.

The bridge picks the wiki-aware runner and sets wiki_query on the sub-agent
context, so an RCA `ask_knowledge_base` lookup can answer from the LLM wiki (and
cite back to the source) instead of chunk-RAG only.
"""

from __future__ import annotations

from agents import RunContextWrapper
from specstar.types import Binary

from workspace_app.agent.tools import read_source_impl
from workspace_app.api.events import MessageDelta
from workspace_app.api.kb_chat_routes import answer_question
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.wiki.orchestrator import WikiAwareRunner
from workspace_app.resources import AgentConfig, Collection, SourceDoc, make_spec


class _WikiAnsweringBase:
    """Reads the cited source in a reader context and answers citing [1]."""

    async def run(self, prompt, ctx):
        if ctx.wiki_sources is not None and ctx.wiki_cite_sources:
            await read_source_impl(RunContextWrapper(ctx), "spec.md")
            yield MessageDelta(text="Per the wiki, zone 3 runs at 245C [1].")
            return
        yield MessageDelta(text="(chunk path — should not be taken)")


async def test_answer_question_with_wiki_true_answers_from_the_wiki_and_cites_the_source():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=False, use_wiki=True))
        .resource_id
    )
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(
            collection_id=cid,
            path="spec.md",
            content=Binary(data=b"Zone 3 setpoint 245C."),
            text="Zone 3 setpoint 245C.",
        ),
        resource_id=encode_doc_id(cid, "spec.md"),
    )

    runner = WikiAwareRunner(_WikiAnsweringBase(), spec)
    answer = await answer_question(
        runner,
        retriever=None,  # type: ignore[arg-type] — wiki-only path never touches it
        collection_ids=[cid],
        question="what is the zone 3 setpoint?",
        agent_config=AgentConfig(name="KB"),
        wiki=True,
    )

    assert "245C" in answer
    # answer_question appends a Sources footer from the resolved [n] citations.
    assert "Sources:" in answer and "spec.md" in answer


async def test_answer_question_without_wiki_does_not_take_the_wiki_path():
    # Same setup, but wiki=False + a base runner whose wiki branch would fire if
    # the context were a reader. With wiki off, ctx.wiki_query is False, so the
    # WikiAwareRunner passes straight through (chunk path).
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="c", use_rag=True, use_wiki=True))
        .resource_id
    )
    runner = WikiAwareRunner(_WikiAnsweringBase(), spec)
    answer = await answer_question(
        runner,
        retriever=None,  # type: ignore[arg-type]
        collection_ids=[cid],
        question="q",
        agent_config=AgentConfig(name="KB"),
        wiki=False,
    )
    assert "chunk path" in answer
