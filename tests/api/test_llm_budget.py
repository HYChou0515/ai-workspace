"""The LLM budget a person still has (docs/plan-llm-budget.md).

The platform records no spending: a deploy brings the numbers in through its own
`ILlmBudget` (`server.llm_budget`), and the platform only shows what it answers.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from workspace_app.api.llm_budget import BudgetLink, BudgetStatus, ILlmBudget
from workspace_app.config.schema import Settings
from workspace_app.factories import get_llm_budget


class StubBudget(ILlmBudget):
    """Module-level so the dotted-path resolver can import it."""

    async def budget_for(self, request: Request, *, user_id: str) -> BudgetStatus | None:
        return BudgetStatus(remaining=11, total=20, link=BudgetLink(label="apply", url="https://x"))


# ─── P1: the seam ───────────────────────────────────────────────────────────


def test_unconfigured_deploy_has_no_budget_source():
    """No ``server.llm_budget`` ⇒ the feature does not exist (D2)."""
    assert Settings().server.llm_budget == ""
    assert get_llm_budget(Settings().server.llm_budget) is None


def test_configured_dotted_path_is_resolved_to_an_instance():
    assert isinstance(get_llm_budget("tests.api.test_llm_budget.StubBudget"), StubBudget)


def test_a_class_that_is_not_a_budget_source_is_refused_at_startup():
    """Loud at boot, naming the key — not a budget row that silently never shows."""
    with pytest.raises(TypeError, match="server.llm_budget.*not an ILlmBudget subclass"):
        get_llm_budget("workspace_app.config.schema.Settings")


def test_every_field_of_the_answer_is_optional():
    """D4: a message alone, numbers alone, or nothing at all are all answers."""
    bare = BudgetStatus()
    assert (bare.remaining, bare.total, bare.message, bare.link) == (None, None, "", None)
    assert bare.tone == "info"
    assert bare.currency == "USD"
