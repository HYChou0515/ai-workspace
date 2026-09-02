"""FallbackModel — busy-aware failover for the agent / sub-agent path.

The KB side wraps ``ILlm`` / ``IVlm``; the agent side speaks the OpenAI Agents
SDK ``Model`` interface, so this is the async sibling of ``FallbackLlm``. It
wraps an ordered :class:`LlmEndpoint` chain, materialising the inner SDK model
for an endpoint (a ``LitellmModel`` / ``RepairingModel`` / …) only when its
turn comes, and shares the SAME process-global cooldown registry as every other
role.

* ``stream_response`` — the live turn. TTFT on the first event (no first event
  within ``ttft_s`` ⇒ the model is busy ⇒ switch + cooldown), idle ceiling on
  the rest; a failure after the first event propagates (a stream the user has
  already seen can't restart).
* ``get_response`` — non-streaming. Switch + cooldown on any error; no TTFT (the
  inner call carries its own timeout).

A 429 is the exception to "switch + cooldown" (#742): the endpoint is healthy
and told us to slow down, so the chain WAITS it out at the same endpoint — the
stated window (never less than the doubling backoff), announced through
``on_hold`` so a long wait is visible instead of reading as a hang — even while
a healthy sibling idles: the operator chose finishing at the limited endpoint
over failing over to it (#759 review, an informed call). The holds are bounded
by time, not count: ``rate_limit_budget_s`` (chain-level from the head, like
``total_deadline_s``; two hours by the operator's call) is a POOL of held
seconds per chain instance — one agent run — shared by every model call that
run makes, because a per-call allowance would reset each tool round and make a
turn's total unbounded again. A window that cannot fit what is left of the pool
parks the endpoint for the window it stated — the shared registry then makes
every other caller honour it — and an exhausted chain raises from the 429
rather than whichever endpoint failed last, so the turn loop upstream can keep
waiting instead of "giving up after N attempts". (The runner may retry the run
with a fresh chain after its own, shorter hold; the operator accepts the rare
stacking that allows — even unbounded waiting was acceptable to them.)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

from agents.models.interface import Model

from .core import AllProvidersFailed, TtftTimeout
from .rate_limit import is_rate_limited, park_for, rate_limit_wait_s

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..factories import LlmEndpoint
    from .cooldown import CooldownRegistry

# Observability hook: (failed model label, cause) when the chain switches.
OnDegrade = Callable[[str, BaseException], None]
# Observability hook: (model label, seconds) when the chain holds at a
# rate-limited endpoint. #742's visibility rule: a held turn must SAY it is
# held — a stated window can be minutes, and silent minutes read as a hang.
OnHold = Callable[[str, float], None]


class FallbackModel(Model):
    def __init__(
        self,
        endpoints: Sequence[LlmEndpoint],
        registry: CooldownRegistry,
        *,
        make_model: Callable[[LlmEndpoint], Model],
        on_switch: OnDegrade | None = None,
        on_hold: OnHold | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._endpoints = list(endpoints)
        self._registry = registry
        self._make_model = make_model
        self._on_switch = on_switch
        self._on_hold = on_hold
        self._sleep = sleep
        # Re-sweep rounds + total deadline are chain-level — taken from the chain
        # head (a fallback's own round budget is ignored, like its `fallbacks`).
        head = self._endpoints[0]
        self._round_backoff_s = head.round_backoff_s
        self._total_deadline_s = head.total_deadline_s
        # Rate-limit holds are bounded by TIME, not count — and by their own
        # budget, not the chain deadline: the deadline (120s in the default
        # deploy) is sized for busy-endpoint sweeps, and a rate limit is the one
        # failure whose honest cure can be much longer than that. The budget is
        # a POOL of held seconds for the instance's lifetime (= one agent run),
        # not a per-call allowance — a reset each model call would make an
        # agentic turn's total hold time unbounded again, one fresh budget per
        # tool round.
        self._rate_limit_budget_s = head.rate_limit_budget_s
        self._held_s = 0.0
        # #748: the endpoint that most recently ANSWERED. Nothing recorded this,
        # so naming a turn's model meant reading the configured one — correct
        # until failover makes it differ, i.e. wrong in exactly the case worth
        # asking about. Starts None: a chain that has not served yet must not
        # claim a model, and defaulting to the head would be the same lie.
        self.served_model: str | None = None

    def _degrade(self, endpoint: LlmEndpoint, cause: BaseException) -> None:
        # `park_for`: the failure cooldown for an unwell endpoint, the STATED
        # window for a rate-limited one (same policy the sync chain uses).
        parked = park_for(endpoint.cooldown_s, cause)
        self._registry.mark(endpoint.cooldown_key, parked)
        logger.warning(
            "failover-model: endpoint %s parked %.1fs after failure (%r) — switching",
            endpoint.model,
            parked,
            cause,
        )
        if self._on_switch is not None:
            self._on_switch(endpoint.model, cause)

    async def _wait_before_round(
        self, backoff: float, keys: Sequence[Any], deadline: float
    ) -> bool:
        """Cooldown-aware async backoff before a re-sweep (mirrors the sync
        ``failover.core._wait_before_round``). ``False`` when the deadline is
        spent so the caller stops and surfaces the busy failure."""
        now = self._registry.now()
        if now >= deadline:
            return False
        wait = min(max(backoff, self._registry.remaining(keys)), deadline - now)
        if wait > 0:
            await self._sleep(wait)
        return True

    async def _hold_for_rate_limit(
        self, endpoint: LlmEndpoint, exc: BaseException, held: int
    ) -> bool:
        """A 429 is the one failure cured by WAITING at the same endpoint
        (#742) — the endpoint is healthy and told us to slow down, so neither
        the quick-retry budget nor the failure cooldown applies to it.

        ``True`` → the wait (the stated window, never less than the doubling
        backoff — `rate_limit_wait_s`) fits what is left of the instance's
        held-seconds pool and has been slept, announced through ``on_hold``
        first; the caller retries the SAME endpoint. ``False`` → it does not
        fit; the endpoint is parked for the window so every caller sharing the
        registry honours it, and the chain moves on to the next endpoint."""
        wait = rate_limit_wait_s(exc, attempt=held)
        if self._held_s + wait > self._rate_limit_budget_s:
            self._registry.mark(endpoint.cooldown_key, wait)
            logger.warning(
                "failover-model: endpoint %s rate-limited for %.1fs — past the "
                "hold budget, parking it for that window and switching",
                endpoint.model,
                wait,
            )
            if self._on_switch is not None:
                self._on_switch(endpoint.model, exc)
            return False
        logger.warning(
            "failover-model: endpoint %s rate-limited (hold %d) — waiting %.1fs before "
            "retrying the same endpoint",
            endpoint.model,
            held,
            wait,
        )
        if self._on_hold is not None:
            self._on_hold(endpoint.model, wait)
        self._held_s += wait
        await self._sleep(wait)
        return True

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        keys = [e.cooldown_key for e in self._endpoints]
        deadline = self._registry.now() + self._total_deadline_s
        last: BaseException | None = None
        rate_limited: BaseException | None = None
        for round_idx in range(len(self._round_backoff_s) + 1):
            if round_idx > 0 and not await self._wait_before_round(
                self._round_backoff_s[round_idx - 1], keys, deadline
            ):
                break
            for endpoint in self._endpoints:
                if self._registry.is_cooling(endpoint.cooldown_key):
                    continue
                logger.debug("failover-model: trying endpoint %s (get_response)", endpoint.model)
                attempt = 0
                held = 0
                while True:
                    try:
                        response = await self._make_model(endpoint).get_response(*args, **kwargs)
                        self.served_model = endpoint.model
                        return response
                    except Exception as exc:  # noqa: BLE001 — any error retries/switches
                        last = exc
                        if is_rate_limited(exc):
                            # Held out of the quick-retry budget: that budget is
                            # for a broken endpoint, and this one is healthy.
                            rate_limited = exc
                            held += 1
                            if await self._hold_for_rate_limit(endpoint, exc, held):
                                continue
                            break  # parked for its stated window → next endpoint
                        attempt += 1
                        if attempt > endpoint.num_retries:
                            # retries exhausted → park it and try the next endpoint.
                            self._degrade(endpoint, exc)
                            break
                        # else: a quick same-endpoint retry
        logger.warning(
            "failover-model: all endpoints failed or cooling (get_response) — last %r",
            last,
        )
        # Chain from the 429 when one was seen: the turn loop upstream tells
        # "rate limited" (wait, own budget, own message) from "broken" (retry
        # hints) by walking exactly this cause chain, and the LAST failure is
        # often the least informative one — a dead spare, not the throttle.
        raise AllProvidersFailed("all agent models failed or were cooling") from (
            rate_limited or last
        )

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        keys = [e.cooldown_key for e in self._endpoints]
        deadline = self._registry.now() + self._total_deadline_s
        last: BaseException | None = None
        rate_limited: BaseException | None = None
        for round_idx in range(len(self._round_backoff_s) + 1):
            if round_idx > 0 and not await self._wait_before_round(
                self._round_backoff_s[round_idx - 1], keys, deadline
            ):
                break
            for endpoint in self._endpoints:
                if self._registry.is_cooling(endpoint.cooldown_key):
                    continue
                logger.debug("failover-model: trying endpoint %s (stream_response)", endpoint.model)
                attempt = 0
                held = 0
                while True:
                    stream = self._make_model(endpoint).stream_response(*args, **kwargs)
                    it = stream.__aiter__()
                    try:
                        first = await asyncio.wait_for(it.__anext__(), timeout=endpoint.ttft_s)
                    except StopAsyncIteration:
                        return  # empty turn — a valid (if useless) success
                    except Exception as exc:  # noqa: BLE001 — any pre-first failure
                        cause = (
                            TtftTimeout(endpoint.model) if isinstance(exc, TimeoutError) else exc
                        )
                        last = cause
                        # The SDK types stream_response as AsyncIterator, but at runtime
                        # it's an async generator — close it so the abandoned inner
                        # stream is torn down rather than left suspended.
                        await cast("AsyncGenerator[Any]", stream).aclose()
                        if is_rate_limited(cause):
                            # Pre-first only, so waiting is safe — nothing the
                            # user saw would be clobbered by the re-send.
                            rate_limited = cause
                            held += 1
                            if await self._hold_for_rate_limit(endpoint, cause, held):
                                continue
                            break  # parked for its stated window → next endpoint
                        attempt += 1
                        if attempt > endpoint.num_retries:
                            # retries exhausted → park it and try the next endpoint.
                            self._degrade(endpoint, cause)
                            break
                        # else: a quick same-endpoint retry (pre-first only)
                    else:
                        # The first event is the proof this endpoint answered —
                        # claim it here, not at the end: a stream that dies
                        # mid-flight still had THIS model write what was seen.
                        self.served_model = endpoint.model
                        yield first
                        while True:
                            try:
                                event = await asyncio.wait_for(
                                    it.__anext__(), timeout=endpoint.idle_s
                                )
                            except StopAsyncIteration:
                                return
                            yield event  # mid-stream errors / idle stalls propagate (terminal)
        logger.warning(
            "failover-model: all endpoints failed or cooling (stream_response) — last %r",
            last,
        )
        # Same cause preference as get_response: surface the 429 when one was
        # seen, so the turn loop can wait it out instead of "giving up".
        raise AllProvidersFailed("all agent models failed or were cooling") from (
            rate_limited or last
        )
