"""An app question can be answered from the wiki, cited back to the source.

This used to be a routing property: `answer_question(wiki=True)` swapped in a
wiki-aware runner that ran the wiki and chunk-RAG side by side and merged them —
which is why "the wiki but not the documents" was unexpressible (#537). It is now
a tool the KB sub-agent chooses: `ask_wiki` delegates to a reader, and what comes
back carries `[n]` markers resolving to the documents the wiki was grounded on.

What's proven end-to-end here is that chain — reader → sub-agent → the app's
answer — with the citation surviving it.
"""

from __future__ import annotations

from agents import RunContextWrapper
from specstar.types import Binary

from workspace_app.agent.ask_kb import AskKbSpec
from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import ask_wiki_impl, read_source_impl
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.api.kb_chat_routes import answer_question
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.retriever import Retriever
from workspace_app.kb.wiki.consult import make_wiki_consultant
from workspace_app.resources import AgentConfig, Collection, SourceDoc, make_spec
from workspace_app.resources.kb import EMBED_DIM


class _Runner:
    """Plays both roles. In a reader context it grounds on the source document and
    cites it; in the KB sub-agent's context it consults the wiki and relays."""

    async def run(self, prompt, ctx: AgentToolContext):
        if ctx.wiki_sources is not None and ctx.wiki_cite_sources:  # the reader
            await read_source_impl(RunContextWrapper(ctx), "spec.md")
            yield MessageDelta(text="Per the wiki, zone 3 runs at 245C [1].")
            return
        yield MessageDelta(text=await ask_wiki_impl(RunContextWrapper(ctx), "what is zone 3?"))
        yield RunDone()


def _collection_with_a_source(spec) -> str:
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="Process", use_rag=True, use_wiki=True))
        .resource_id
    )
    text = "Zone 3 setpoint 245C."
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(collection_id=cid, path="spec.md", content=Binary(data=text.encode()), text=text),
        resource_id=encode_doc_id(cid, "spec.md"),
    )
    return cid


async def test_an_app_question_answered_from_the_wiki_cites_the_source_document():
    spec = make_spec(default_user="u")
    cid = _collection_with_a_source(spec)
    runner = _Runner()

    answer = await answer_question(
        runner,
        Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM)),
        [cid],
        "what is zone 3?",
        agent_config=AgentConfig(name="kb", model="x", allowed_tools=["kb_search", "ask_wiki"]),
        spec=spec,
        wiki_consultant_factory=lambda cids: make_wiki_consultant(runner, spec, cids),
    )

    assert "245C" in answer
    # The citation reaches the app's answer pointing at the DOCUMENT, not at the
    # synthesized wiki page — so the claim stays auditable through two hops.
    assert "spec.md" in answer


async def test_documents_can_be_left_alone_while_the_wiki_answers():
    """`use_rag` stays on and nothing forces a document search alongside. The old
    routing ran both and merged them whenever the collection had each enabled —
    exactly the coupling #537 reports."""
    spec = make_spec(default_user="u")
    cid = _collection_with_a_source(spec)
    seen: list[list[str] | None] = []

    class _Capture(_Runner):
        async def run(self, prompt, ctx):
            if ctx.wiki_sources is None:  # the KB sub-agent, not the reader
                seen.append(ctx.agent_config.allowed_tools if ctx.agent_config else None)
            async for ev in super().run(prompt, ctx):
                yield ev

    runner = _Capture()
    await answer_question(
        runner,
        Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM)),
        [cid],
        "q",
        agent_config=AgentConfig(name="kb", model="x", allowed_tools=["kb_search", "ask_wiki"]),
        spec=spec,
        ask_kb_spec=AskKbSpec(kb_search_max=0, wiki_search_max=2, glossary=False),
        wiki_consultant_factory=lambda cids: make_wiki_consultant(runner, spec, cids),
    )

    assert seen == [["ask_wiki"]]
