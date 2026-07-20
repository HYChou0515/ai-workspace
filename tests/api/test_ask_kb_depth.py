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

from workspace_app.agent import KbSearchBudget, kb_search_impl
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.api.kb_chat_routes import answer_question
from workspace_app.kb.retriever import Enhancements
from workspace_app.resources import AgentConfig


def test_ask_knowledge_base_depth_reaches_kb_search():
    recorded: dict[str, object] = {}

    class _RecordingRetriever:
        # **kw: these doubles assert the DEPTH/budget plumbing, not the query's scope,
        # so they shouldn't break every time retrieval gains a filter.
        def search(self, query, collection_ids, on_progress, *, enhancements=None, **kw):
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
            _ToolCallingRunner(),  # ty: ignore[invalid-argument-type]
            _RecordingRetriever(),  # ty: ignore[invalid-argument-type]
            ["c1"],
            "what does the KB say about reflow",
            agent_config=AgentConfig(name="kb"),
            enhancements=Enhancements(expand=3, hyde=1, rerank=True),
        )
    )

    assert recorded["enh"] == Enhancements(expand=3, hyde=1, rerank=True)


def test_ask_knowledge_base_caps_kb_search_calls():
    """#195: answer_question threads `max_searches` into the bridge's ctx, so the
    KB sub-agent can't run kb_search past the cap — a 2nd call when the cap is 1
    returns the sentinel without ever touching the retriever."""
    calls = {"n": 0}
    captured: dict[str, str] = {}

    class _CountingRetriever:
        # **kw: these doubles assert the DEPTH/budget plumbing, not the query's scope,
        # so they shouldn't break every time retrieval gains a filter.
        def search(self, query, collection_ids, on_progress, *, enhancements=None, **kw):
            calls["n"] += 1
            return []

    class _TwoSearchRunner:
        async def run(self, question, ctx):
            wrapped = RunContextWrapper(ctx)
            kb_search_impl(wrapped, "first")  # uses the single unit of budget
            captured["second"] = kb_search_impl(wrapped, "second")  # exhausted
            yield MessageDelta(text="done")
            yield RunDone()

    asyncio.run(
        answer_question(
            _TwoSearchRunner(),  # ty: ignore[invalid-argument-type]
            _CountingRetriever(),  # ty: ignore[invalid-argument-type]
            ["c1"],
            "q",
            agent_config=AgentConfig(name="kb"),
            max_searches=1,
        )
    )

    assert calls["n"] == 1  # the cap blocked the second real search
    assert "budget exhausted" in captured["second"].lower()


def test_shared_budget_spans_multiple_ask_knowledge_base_calls():
    """#334 Q6: an app turn shares ONE budget across its ask_knowledge_base calls.

    With a shared cap of 1, the FIRST sub-agent's search runs but the SECOND
    call's search is already exhausted — proving the counter is shared across
    calls, not reset per call (a fresh per-call budget would let the second run).
    """
    calls = {"n": 0}
    captured: dict[str, str] = {}

    class _CountingRetriever:
        # **kw: these doubles assert the DEPTH/budget plumbing, not the query's scope,
        # so they shouldn't break every time retrieval gains a filter.
        def search(self, query, collection_ids, on_progress, *, enhancements=None, **kw):
            calls["n"] += 1
            return []

    class _OneSearchRunner:
        async def run(self, question, ctx):
            captured["out"] = kb_search_impl(RunContextWrapper(ctx), question)
            yield MessageDelta(text="done")
            yield RunDone()

    budget = KbSearchBudget(max_calls=1)
    retriever = _CountingRetriever()

    async def two_calls():
        for q in ("first question", "second question"):
            await answer_question(
                _OneSearchRunner(),  # ty: ignore[invalid-argument-type]
                retriever,  # ty: ignore[invalid-argument-type]
                ["c1"],
                q,
                agent_config=AgentConfig(name="kb"),
                budget=budget,
            )

    asyncio.run(two_calls())

    assert calls["n"] == 1  # only the first call's search actually touched the retriever
    assert "budget exhausted" in captured["out"].lower()  # the second was exhausted
