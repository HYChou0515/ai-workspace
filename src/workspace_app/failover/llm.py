"""FallbackLlm / FallbackVlm — busy-aware adapters over the failover core.

Each wraps an ordered :class:`LlmEndpoint` chain behind the existing ``ILlm`` /
``IVlm`` seam, so every caller (KB retrieval, VLM describe, formatter, …) gets
failover for free with no call-site change. Endpoints are materialised lazily
(``make_llm`` / ``make_vlm`` is called only when an endpoint's turn comes), so a
cooling endpoint is never even constructed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

from ..kb.llm import ILlm
from ..kb.vlm.protocol import IVlm
from .core import OnSwitch, Provider, failover_stream

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
    ) -> None:
        self._endpoints = list(endpoints)
        self._registry = registry
        self._make_llm = make_llm
        self._on_switch = on_switch

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
            )
            for e in self._endpoints
        ]
        yield from failover_stream(
            providers, self._registry, on_switch=_on_switch_adapter(self._on_switch)
        )


class FallbackVlm(IVlm):
    def __init__(
        self,
        endpoints: Sequence[LlmEndpoint],
        registry: CooldownRegistry,
        *,
        make_vlm: Callable[[LlmEndpoint], IVlm],
        on_switch: OnDegrade | None = None,
    ) -> None:
        self._endpoints = list(endpoints)
        self._registry = registry
        self._make_vlm = make_vlm
        self._on_switch = on_switch

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        def starter(endpoint: LlmEndpoint) -> Callable[[], Iterator[tuple[str, bool]]]:
            return lambda: self._make_vlm(endpoint).stream(prompt, images=images)

        providers = [
            Provider(
                key=e.cooldown_key,
                label=e.model,
                start=starter(e),
                ttft_s=e.ttft_s,
                idle_s=e.idle_s,
                cooldown_s=e.cooldown_s,
            )
            for e in self._endpoints
        ]
        yield from failover_stream(
            providers, self._registry, on_switch=_on_switch_adapter(self._on_switch)
        )
