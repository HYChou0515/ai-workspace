"""failover_stream — the strict-priority, busy-aware switching loop.

Generic over the streamed item type so the SAME policy drives every role: the
caller hands an ordered list of :class:`Provider` (each knows how to *start* a
stream and carries its own TTFT / idle / cooldown budget) plus the shared
:class:`CooldownRegistry`. We try them in priority order, skipping any that are
cooling, and:

* a failure (or TTFT timeout) **before the first item** ⇒ park the provider on
  cooldown and switch to the next — a busy model stays busy, so *switching* is
  the retry (same-model retry ≈ 0);
* the first successful provider's items are yielded straight through;
* a failure (or idle stall) **after the first item** ⇒ propagate — a stream the
  caller has already started reading can't be transparently restarted;
* every provider exhausted ⇒ :class:`AllProvidersFailed`.

TTFT / idle are enforced by draining each provider's (blocking, sync) iterator on
a daemon producer thread and reading items off a queue with a wall-clock
deadline; cooldown timing uses the registry's injected clock. The inner LLM call
is expected to carry its own ``timeout`` so an abandoned producer eventually dies.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Hashable, Iterator, Sequence
from dataclasses import dataclass
from typing import cast

from .cooldown import CooldownRegistry

# Called when a tried provider fails before its first item (a switch). Receives
# the provider and the underlying cause (a TtftTimeout for a silent stall).
type OnSwitch[T] = Callable[[Provider[T], BaseException], None]


class AllProvidersFailed(RuntimeError):
    """Every provider in the chain failed (or was cooling) before output."""


class TtftTimeout(TimeoutError):
    """No first token arrived within the provider's ``ttft_s`` — treated as busy."""


class StreamStalled(TimeoutError):
    """A stream went silent mid-flight for longer than ``idle_s`` — terminal."""


@dataclass(frozen=True)
class Provider[T]:
    """One entry in a role's priority chain.

    ``start`` begins the stream; its first ``next()`` may block (network) or
    raise. ``key`` is the cooldown identity (shared across roles — use
    ``(model, endpoint)``). ``label`` is for logs/observability.
    """

    key: Hashable
    label: str
    start: Callable[[], Iterator[T]]
    ttft_s: float
    idle_s: float
    cooldown_s: float


# Internal sentinel: a failure that happened before the first item was yielded,
# so the loop may switch. Carries the underlying cause.
class _PreFirstFailure(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__()
        self.cause = cause


def _drive[T](provider: Provider[T]) -> Iterator[T]:
    """Yield a provider's items, enforcing TTFT on the first and idle on the rest.

    Raises ``_PreFirstFailure`` if it fails before the first item (switchable);
    propagates the original error / ``StreamStalled`` if it fails after (terminal).
    """
    q: queue.Queue[tuple[str, object]] = queue.Queue()

    def produce() -> None:
        try:
            for item in provider.start():
                q.put(("item", item))
            q.put(("done", None))
        except BaseException as exc:  # noqa: BLE001 — relayed to the consumer
            q.put(("error", exc))

    threading.Thread(target=produce, daemon=True).start()

    try:
        kind, payload = q.get(timeout=provider.ttft_s)
    except queue.Empty:
        raise _PreFirstFailure(TtftTimeout(provider.label)) from None
    if kind == "error":
        assert isinstance(payload, BaseException)
        raise _PreFirstFailure(payload)
    if kind == "done":
        return  # empty completion — valid per the ILlm contract
    yield cast("T", payload)  # first item

    while True:
        try:
            kind, payload = q.get(timeout=provider.idle_s)
        except queue.Empty:
            raise StreamStalled(provider.label) from None
        if kind == "error":
            assert isinstance(payload, BaseException)
            raise payload
        if kind == "done":
            return
        yield cast("T", payload)


def failover_stream[T](
    providers: Sequence[Provider[T]],
    cooldown: CooldownRegistry,
    *,
    on_switch: OnSwitch[T] | None = None,
) -> Iterator[T]:
    last: BaseException | None = None
    for provider in providers:
        if cooldown.is_cooling(provider.key):
            continue
        try:
            yield from _drive(provider)
            return
        except _PreFirstFailure as failure:
            cooldown.mark(provider.key, provider.cooldown_s)
            last = failure.cause
            if on_switch is not None:
                on_switch(provider, failure.cause)
            continue
    raise AllProvidersFailed("all providers failed or were cooling") from last


@dataclass(frozen=True)
class CallProvider[R]:
    """A non-streaming failover entry (e.g. an embedding request) — ``call``
    either returns the whole result or raises. There is no TTFT/idle here: the
    inner call carries its own total ``timeout``, and any error switches."""

    key: Hashable
    label: str
    call: Callable[[], R]
    cooldown_s: float


def failover_call[R](
    providers: Sequence[CallProvider[R]],
    cooldown: CooldownRegistry,
    *,
    on_switch: Callable[[CallProvider[R], BaseException], None] | None = None,
) -> R:
    """Try each non-cooling provider in priority order; the first that returns
    wins. Any error parks that provider on cooldown and switches to the next.
    All exhausted ⇒ :class:`AllProvidersFailed`."""
    last: BaseException | None = None
    for provider in providers:
        if cooldown.is_cooling(provider.key):
            continue
        try:
            return provider.call()
        except Exception as exc:  # noqa: BLE001 — any failure switches to the next
            cooldown.mark(provider.key, provider.cooldown_s)
            last = exc
            if on_switch is not None:
                on_switch(provider, exc)
    raise AllProvidersFailed("all providers failed or were cooling") from last
