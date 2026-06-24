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
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

from agents.models.interface import Model

from .core import AllProvidersFailed, TtftTimeout

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
    ) -> None:
        self._endpoints = list(endpoints)
        self._registry = registry
        self._make_model = make_model
        self._on_switch = on_switch

    def _degrade(self, endpoint: LlmEndpoint, cause: BaseException) -> None:
        self._registry.mark(endpoint.cooldown_key, endpoint.cooldown_s)
        if self._on_switch is not None:
            self._on_switch(endpoint.model, cause)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        last: BaseException | None = None
        for endpoint in self._endpoints:
            if self._registry.is_cooling(endpoint.cooldown_key):
                continue
            try:
                return await self._make_model(endpoint).get_response(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — any error switches to the next
                self._degrade(endpoint, exc)
                last = exc
        raise AllProvidersFailed("all agent models failed or were cooling") from last

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        last: BaseException | None = None
        for endpoint in self._endpoints:
            if self._registry.is_cooling(endpoint.cooldown_key):
                continue
            stream = self._make_model(endpoint).stream_response(*args, **kwargs)
            it = stream.__aiter__()
            try:
                first = await asyncio.wait_for(it.__anext__(), timeout=endpoint.ttft_s)
            except StopAsyncIteration:
                return  # empty turn — a valid (if useless) success
            except Exception as exc:  # noqa: BLE001 — any pre-first failure ⇒ switch
                cause = TtftTimeout(endpoint.model) if isinstance(exc, TimeoutError) else exc
                self._degrade(endpoint, cause)
                last = cause
                # The SDK types stream_response as AsyncIterator, but at runtime
                # it's an async generator — close it so the abandoned inner
                # stream is torn down rather than left suspended.
                await cast("AsyncGenerator[Any]", stream).aclose()
                continue
            yield first
            while True:
                try:
                    event = await asyncio.wait_for(it.__anext__(), timeout=endpoint.idle_s)
                except StopAsyncIteration:
                    return
                yield event  # mid-stream errors / idle TimeoutError propagate (terminal)
            return
        raise AllProvidersFailed("all agent models failed or were cooling") from last
