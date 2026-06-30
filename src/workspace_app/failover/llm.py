"""FallbackLlm / FallbackVlm — busy-aware adapters over the failover core.

Each wraps an ordered :class:`LlmEndpoint` chain behind the existing ``ILlm`` /
``IVlm`` seam, so every caller (KB retrieval, VLM describe, formatter, …) gets
failover for free with no call-site change. Endpoints are materialised lazily
(``make_llm`` / ``make_vlm`` is called only when an endpoint's turn comes), so a
cooling endpoint is never even constructed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

from ..kb.llm import ILlm
from ..kb.vlm.protocol import IVlm
from .core import CallProvider, OnSwitch, Provider, failover_stream
from .retry import call_with_failover

if TYPE_CHECKING:
    from ..factories import LlmEndpoint
    from .cooldown import CooldownRegistry

# Observability hook: (failed model label, cause) when the chain switches.
OnDegrade = Callable[[str, BaseException], None]


def _on_switch_adapter(on_degrade: OnDegrade | None) -> OnSwitch | None:
    if on_degrade is None:
        return None
    return lambda provider, cause: on_degrade(provider.label, cause)


class FallbackLlm(ILlm):
    def __init__(
        self,
        endpoints: Sequence[LlmEndpoint],
        registry: CooldownRegistry,
        *,
        make_llm: Callable[[LlmEndpoint], ILlm],
        on_switch: OnDegrade | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._endpoints = list(endpoints)
        self._registry = registry
        self._make_llm = make_llm
        self._on_switch = on_switch
        self._sleep = sleep

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        def starter(endpoint: LlmEndpoint) -> Callable[[], Iterator[tuple[str, bool]]]:
            return lambda: self._make_llm(endpoint).stream(prompt)

        providers = [
            Provider(
                key=e.cooldown_key,
                label=e.model,
                start=starter(e),
                ttft_s=e.ttft_s,
                idle_s=e.idle_s,
                cooldown_s=e.cooldown_s,
                num_retries=e.num_retries,
            )
            for e in self._endpoints
        ]
        # Re-sweep rounds + total deadline are chain-level — read from the head
        # endpoint (a fallback's own round budget is ignored, like its fallbacks).
        head = self._endpoints[0]
        yield from failover_stream(
            providers,
            self._registry,
            round_backoff_s=head.round_backoff_s,
            total_deadline_s=head.total_deadline_s,
            on_switch=_on_switch_adapter(self._on_switch),
            sleep=self._sleep,
        )


class FallbackVlm(IVlm):
    """#249: a VLM description is consumed only after it is fully accumulated
    (it is indexed, not shown live token-by-token), so the WHOLE describe is one
    retryable unit. Each attempt drains one endpoint to completion; a transient
    blip retries the chain with backoff, a permanent error aborts. The result is
    yielded as a single chunk so the base ``collect`` still works on top."""

    def __init__(
        self,
        endpoints: Sequence[LlmEndpoint],
        *,
        make_vlm: Callable[[LlmEndpoint], IVlm],
        on_switch: OnDegrade | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._endpoints = list(endpoints)
        self._make_vlm = make_vlm
        self._on_switch = on_switch
        self._sleep = sleep

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        def call_at(endpoint: LlmEndpoint) -> Callable[[], str]:
            return lambda: self._make_vlm(endpoint).collect(prompt, images=images)

        providers = [
            CallProvider(key=e.cooldown_key, label=e.model, call=call_at(e), cooldown_s=0.0)
            for e in self._endpoints
        ]
        degrade = self._on_switch

        def on_switch(provider: CallProvider, cause: BaseException) -> None:
            if degrade is not None:
                degrade(provider.label, cause)

        head = self._endpoints[0]
        text = call_with_failover(
            providers,
            m=head.num_retries + 1,
            round_delays=head.round_backoff_s,
            sleep=self._sleep,
            on_switch=on_switch,
        )
        yield (text, False)
