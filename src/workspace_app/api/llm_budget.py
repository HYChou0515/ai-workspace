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
import asyncio
import logging
import time
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, Request
from msgspec import Struct
from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: How long one person's answer is reused (D6): the class is asked at most once
#: per person per this many seconds, however many chats open and turns end.
CACHE_S = 30.0
#: How long the platform waits for the class before saying 「預算暫時查不到」
#: (D7). The budget is a glance, not a gate: a slow cost system must not keep the
#: row loading — and it never holds anything else up.
TIMEOUT_S = 3.0


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


# ─── GET /me/llm-budget ─────────────────────────────────────────────────────


class BudgetLinkOut(BaseModel):
    label: str
    url: str


class BudgetOut(BaseModel):
    remaining: float | None = None
    total: float | None = None
    currency: str = "USD"
    message: str = ""
    link: BudgetLinkOut | None = None
    tone: Literal["info", "warn", "critical"] = "info"


class LlmBudgetOut(BaseModel):
    """``off`` — the deploy configured no class: draw nothing. ``ok`` — the
    class answered; ``budget`` is null when it had nothing to show.
    ``unavailable`` — it raised or did not answer in time: 「預算暫時查不到」."""

    status: Literal["off", "ok", "unavailable"]
    budget: BudgetOut | None = None


def _safe_link(link: BudgetLink | None, *, user_id: str) -> BudgetLinkOut | None:
    """The URL comes from the deploy's code and lands in an ``<a href>``: only
    http(s) is drawn — a ``javascript:`` one would run in the person's page."""
    if link is None:
        return None
    if urlparse(link.url).scheme.lower() not in ("http", "https"):
        logger.warning("llm budget: dropped a non-http link for %s: %r", user_id, link.url)
        return None
    return BudgetLinkOut(label=link.label, url=link.url)


def _out(status: BudgetStatus | None, *, user_id: str) -> LlmBudgetOut:
    if status is None:
        return LlmBudgetOut(status="ok")
    return LlmBudgetOut(
        status="ok",
        budget=BudgetOut(
            remaining=status.remaining,
            total=status.total,
            currency=status.currency,
            message=status.message,
            link=_safe_link(status.link, user_id=user_id),
            tone=status.tone,
        ),
    )


class BudgetCache:
    """Ask the deploy's class about one person at most once per ``ttl_s``, and
    only once at a time: asks that arrive while one is in flight share it.

    An ``unavailable`` answer is kept for the same window — a cost system that
    is down is not asked again by every chat that opens meanwhile."""

    def __init__(
        self,
        source: ILlmBudget,
        *,
        timeout_s: float = TIMEOUT_S,
        ttl_s: float = CACHE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source = source
        self._timeout_s = timeout_s
        self._ttl_s = ttl_s
        self._clock = clock
        self._done: dict[str, tuple[float, LlmBudgetOut]] = {}
        self._pending: dict[str, asyncio.Future[LlmBudgetOut]] = {}

    async def status_for(self, request: Request, *, user_id: str) -> LlmBudgetOut:
        now = self._clock()
        hit = self._done.get(user_id)
        if hit is not None and now < hit[0]:
            return hit[1]
        task = self._pending.get(user_id)
        if task is None:
            task = asyncio.ensure_future(self._ask(request, user_id))
            self._pending[user_id] = task
            task.add_done_callback(lambda _t, u=user_id: self._pending.pop(u, None))
        # Shielded: one caller giving up (a closed tab) must not cancel the
        # answer everyone else in flight is waiting for.
        return await asyncio.shield(task)

    async def _ask(self, request: Request, user_id: str) -> LlmBudgetOut:
        try:
            answer = await asyncio.wait_for(
                self._source.budget_for(request, user_id=user_id),
                timeout=self._timeout_s,
            )
            out = _out(answer, user_id=user_id)
        except TimeoutError:
            logger.warning("llm budget: no answer for %s within %.1fs", user_id, self._timeout_s)
            out = LlmBudgetOut(status="unavailable")
        except Exception as exc:  # the deploy's code: anything it raises is "unavailable"
            logger.warning("llm budget: the budget class failed for %s: %s", user_id, exc)
            out = LlmBudgetOut(status="unavailable")
        now = self._clock()
        # Bounded by the people asking within one window: expired rows go here.
        for gone in [u for u, (until, _) in self._done.items() if until <= now]:
            del self._done[gone]
        self._done[user_id] = (now + self._ttl_s, out)
        return out


def register_llm_budget_routes(
    app: FastAPI | APIRouter,
    *,
    llm_budget: ILlmBudget | None,
    get_user_id: Callable[[], str],
    timeout_s: float = TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """``GET /me/llm-budget`` — the asking person's own budget, never anyone
    else's: there is no user parameter (only yourself)."""
    cache = BudgetCache(llm_budget, timeout_s=timeout_s, clock=clock) if llm_budget else None

    @app.get("/me/llm-budget", response_model=LlmBudgetOut)
    async def my_llm_budget(request: Request) -> LlmBudgetOut:
        if cache is None:
            return LlmBudgetOut(status="off")
        return await cache.status_for(request, user_id=get_user_id())
