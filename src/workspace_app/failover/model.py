"""FallbackModel — busy-aware failover for the agent / sub-agent path.

The KB side wraps ``ILlm`` / ``IVlm``; the agent side speaks the OpenAI Agents
SDK ``Model`` interface, so this is the async sibling of ``FallbackLlm``. It
wraps an ordered :class:`LlmEndpoint` chain, materialising the inner SDK model
for an endpoint (a ``LitellmModel`` / ``DecideThenActModel`` / …) only when its
turn comes, and shares the SAME process-global cooldown registry as every other
role.

* ``stream_response`` — the live turn. TTFT on the first event (no first event
  within ``ttft_s`` ⇒ the model is busy ⇒ switch + cooldown), idle ceiling on
  the rest; a failure after the first event propagates (a stream the user has
  already seen can't restart).
* ``get_response`` — non-streaming. Switch + cooldown on any error; no TTFT (the
  inner call carries its own timeout).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

from agents.models.interface import Model

from .core import AllProvidersFailed, TtftTimeout

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..factories import LlmEndpoint
    from .cooldown import CooldownRegistry

# Observability hook: (failed model label, cause) when the chain switches.
OnDegrade = Callable[[str, BaseException], None]


class FallbackModel(Model):
    def __init__(
        self,
        endpoints: Sequence[LlmEndpoint],
        registry: CooldownRegistry,
        *,
        make_model: Callable[[LlmEndpoint], Model],
        on_switch: OnDegrade | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._endpoints = list(endpoints)
        self._registry = registry
        self._make_model = make_model
        self._on_switch = on_switch
        self._sleep = sleep
        # Re-sweep rounds + total deadline are chain-level — taken from the chain
        # head (a fallback's own round budget is ignored, like its `fallbacks`).
        head = self._endpoints[0]
        self._round_backoff_s = head.round_backoff_s
        self._total_deadline_s = head.total_deadline_s

    def _degrade(self, endpoint: LlmEndpoint, cause: BaseException) -> None:
        self._registry.mark(endpoint.cooldown_key, endpoint.cooldown_s)
        logger.warning(
            "failover-model: endpoint %s parked %.1fs after failure (%r) — switching",
            endpoint.model,
            endpoint.cooldown_s,
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

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        keys = [e.cooldown_key for e in self._endpoints]
        deadline = self._registry.now() + self._total_deadline_s
        last: BaseException | None = None
        for round_idx in range(len(self._round_backoff_s) + 1):
            if round_idx > 0 and not await self._wait_before_round(
                self._round_backoff_s[round_idx - 1], keys, deadline
            ):
                break
            for endpoint in self._endpoints:
                if self._registry.is_cooling(endpoint.cooldown_key):
                    continue
                logger.debug("failover-model: trying endpoint %s (get_response)", endpoint.model)
                for attempt in range(endpoint.num_retries + 1):
                    try:
                        return await self._make_model(endpoint).get_response(*args, **kwargs)
                    except Exception as exc:  # noqa: BLE001 — any error retries/switches
                        last = exc
                        if attempt == endpoint.num_retries:
                            # retries exhausted → park it; the loop ends naturally
                            # and the next endpoint is tried.
                            self._degrade(endpoint, exc)
                        # else: a quick same-endpoint retry
        logger.warning(
            "failover-model: all endpoints failed or cooling (get_response) — last %r",
            last,
        )
        raise AllProvidersFailed("all agent models failed or were cooling") from last

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        keys = [e.cooldown_key for e in self._endpoints]
        deadline = self._registry.now() + self._total_deadline_s
        last: BaseException | None = None
        for round_idx in range(len(self._round_backoff_s) + 1):
            if round_idx > 0 and not await self._wait_before_round(
                self._round_backoff_s[round_idx - 1], keys, deadline
            ):
                break
            for endpoint in self._endpoints:
                if self._registry.is_cooling(endpoint.cooldown_key):
                    continue
                logger.debug("failover-model: trying endpoint %s (stream_response)", endpoint.model)
                for attempt in range(endpoint.num_retries + 1):
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
                        if attempt == endpoint.num_retries:
                            # retries exhausted → park it; the loop ends naturally
                            # and the next endpoint is tried.
                            self._degrade(endpoint, cause)
                        # else: a quick same-endpoint retry (pre-first only)
                    else:
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
        raise AllProvidersFailed("all agent models failed or were cooling") from last
