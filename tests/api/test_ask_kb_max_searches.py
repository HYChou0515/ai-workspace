"""#334: the composer's per-message kb_search-count pick reaches the KB
sub-agent that `ask_knowledge_base` spawns, and ONE budget is shared across
every ask_knowledge_base call in the app turn (Q6 — the whole turn, not each
sub-agent, gets N searches).

The seam: the RCA message endpoint → ChatSendService.send builds one
KbSearchBudget from `body.max_kb_searches` → the _run_subagent_with_depth
closure passes that SAME object to the bridge for every kb_chat call →
answer_question sets it on each sub-agent's AgentToolContext.
"""

from __future__ import annotations

from workspace_app.api import create_app
from workspace_app.api.events import MessageDelta
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


def _budgets_seen_for(max_kb_searches: int | None) -> list:
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    seen: list = []

    class _Runner:
        async def run(self, prompt, ctx):  # noqa: ANN001 — test double
            if ctx.sandbox is None:  # the KB sub-agent turn (KB flavour, no sandbox)
                seen.append(ctx.kb_search_budget)
                yield MessageDelta(text="kb answer")
                return
            # Two ask_knowledge_base calls in ONE outer turn.
            await ctx.run_subagent("kb_chat", "first")  # type: ignore[misc]
            await ctx.run_subagent("kb_chat", "second")  # type: ignore[misc]
            yield MessageDelta(text="done")

    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_Runner()
    )
    client = TestClient(app)
    body: dict = {"content": "q"}
    if max_kb_searches is not None:
        body["max_kb_searches"] = max_kb_searches
    client.post(f"/a/rca/items/{iid}/messages", json=body)
    return seen


def test_per_message_pick_reaches_subagent_and_is_shared_across_calls():
    seen = _budgets_seen_for(2)
    assert len(seen) == 2
    assert seen[0].max_calls == 2  # the composer's pick reached the sub-agent
    assert seen[0] is seen[1]  # Q6: ONE budget shared across both ask_kb calls


def test_zero_pick_disables_search_for_the_whole_turn():
    seen = _budgets_seen_for(0)
    assert seen[0].max_calls == 0  # 0 = don't search this reply (#334 Q4)
    assert seen[0] is seen[1]


def test_absent_pick_falls_back_to_operator_default():
    # create_app's default operator cap is None (unlimited) when not configured.
    seen = _budgets_seen_for(None)
    assert seen[0].max_calls is None
