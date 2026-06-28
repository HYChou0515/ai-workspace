"""Deterministic cov-fill for the ask_knowledge_base "no priority tier" branch.

When an AgentToolContext has NO collection_tiers configured (the default,
whole-KB case) but the agent calls ask_knowledge_base with rank>0, the tool
short-circuits with an explanatory message and appends one empty citation
bucket entry (preserving the per-tool-name pairing). This drives that branch
directly (no LLM / sub-agent needed — the early return never calls run_subagent).
"""

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl


async def _boom(*_args, **_kwargs):  # pragma: no cover - must NOT be reached
    raise AssertionError("run_subagent must not be called on the early return")


async def test_ask_kb_rank_without_tiers_returns_no_tier_message() -> None:
    # No collection_tiers ⇒ n == 0, and rank > 0 ⇒ the "no priority tier" branch.
    context = AgentToolContext(run_subagent=_boom)
    assert context.collection_tiers == []
    ctx = RunContextWrapper(context)

    result = await ask_knowledge_base_impl(ctx, "why did the build fail?", rank=1)

    assert "no priority tier 1" in result
    assert "isn't" in result and "organised into tiers" in result
    assert "without a rank" in result

    # Exactly one bucket entry appended (an empty one) so citation pairing holds.
    assert context.subagent_citations["ask_knowledge_base"] == [[]]
