"""ILlmBudget — the seam a deploy brings a person's remaining LLM budget in
through (docs/plan-llm-budget.md).

The platform records no spending and sets no limit (D1): what a person may
still spend on the LLM is known to the deploy's own gateway or cost system, so
the platform only ASKS — through the class a deploy names in
``server.llm_budget`` — and shows the answer above the chat composers.

Shown, never enforced (D5): nothing here refuses a send. The gateway is what
refuses when the money runs out; a slow or wrong answer from this seam must
never be able to stop someone who still has money. And the AI is never told
(D8) — the figure is for the person.

The platform ships no implementation, as with ``IRequestEnv``: which system,
which credential and what the numbers mean belong to the deploy.
"""

from __future__ import annotations

import abc
from typing import Literal

from fastapi import Request
from msgspec import Struct


class BudgetLink(Struct, kw_only=True, frozen=True):
    """One link shown after the figure — e.g. where to ask for more."""

    label: str
    url: str


class BudgetStatus(Struct, kw_only=True, frozen=True):
    """What to show about one person's budget. Every field is optional (D4):
    numbers alone, a message alone, or both.

    ``remaining`` / ``total`` are amounts in ``currency``; the platform formats
    them (remaining first: 「剩 $11／共 $20」). ``message`` is one line of PLAIN
    text — never rendered as HTML or markdown. ``tone`` says how much it
    matters; the page says it in words as well as colour."""

    remaining: float | None = None
    total: float | None = None
    currency: str = "USD"
    message: str = ""
    link: BudgetLink | None = None
    tone: Literal["info", "warn", "critical"] = "info"


class ILlmBudget(abc.ABC):
    """Answer how much LLM budget one person has left.

    Resolved once at startup from the ``server.llm_budget`` dotted path, so the
    impl must be constructible with no arguments.
    """

    @abc.abstractmethod
    async def budget_for(self, request: Request, *, user_id: str) -> BudgetStatus | None:
        """This person's budget, or ``None`` for "nothing to show".

        ``request`` is the person's own request (``GET /me/llm-budget``), so the
        impl can use the credential its gateway attached rather than keeping one
        of its own (D3). ``user_id`` is who is asking — always the person
        themselves; the platform never asks about anyone else.

        Asked when a chat opens and when a turn ends, at most once per person
        per 30 s (D6). Raising, or taking longer than the platform waits (3 s),
        shows 「預算暫時查不到」 and is logged (D7) — it never fails anything
        else."""
