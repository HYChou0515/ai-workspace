"""Does the composer's Knowledge-search depth actually reach kb_search when the
RCA agent uses ask_knowledge_base?

The chain is: send_message → caller_enh → _run_subagent → answer_question
(ctx.kb_enhancements=…) → the KB sub-agent's kb_search reads ctx.kb_enhancements
and forwards it to the retriever. This test drives answer_question with a runner
that actually invokes kb_search + a retriever that records the enhancements it
received, so a broken link (kb_search NOT receiving the depth) fails loudly.
"""

from __future__ import annotations

import asyncio

from agents import RunContextWrapper

from workspace_app.agent import kb_search_impl
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.api.kb_chat_routes import answer_question
from workspace_app.kb.retriever import Enhancements
from workspace_app.resources import AgentConfig


def test_ask_knowledge_base_depth_reaches_kb_search():
    recorded: dict[str, object] = {}

    class _RecordingRetriever:
        def search(self, query, collection_ids, on_progress, *, enhancements=None):
            recorded["enh"] = enhancements
            return []

    class _ToolCallingRunner:
        """Stands in for the KB sub-agent: invokes kb_search with the SAME ctx
        answer_question built — exactly what the real agent loop does."""

        async def run(self, question, ctx):
            kb_search_impl(RunContextWrapper(ctx), question)
            yield MessageDelta(text="done")
            yield RunDone()

    asyncio.run(
        answer_question(
            _ToolCallingRunner(),
            _RecordingRetriever(),
            ["c1"],
            "what does the KB say about reflow",
            agent_config=AgentConfig(name="kb"),
            enhancements=Enhancements(expand=3, hyde=1, rerank=True),
        )
    )

    assert recorded["enh"] == Enhancements(expand=3, hyde=1, rerank=True)
