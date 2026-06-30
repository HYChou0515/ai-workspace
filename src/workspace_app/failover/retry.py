"""Transient-error retry for index-time model calls (#249).

``failover_stream`` (chat) treats a busy model as "switch, don't retry — a busy
model stays busy". That is wrong for a transient *gateway* blip (a 502 while
Ollama restarts, a dropped connection, a timeout): waiting a beat and re-calling
the SAME endpoint recovers it, and on a single-endpoint deploy there is nowhere
to switch to. These two helpers are that temporal retry, kept side by side so the
whole flow reads top-to-bottom:

* :func:`try_provider` — give ONE provider ``m`` quick attempts (a fixed ``gap``
  apart) before giving up on it;
* :func:`call_with_failover` — sweep every provider in priority order (switch on
  the first transient failure); if a whole sweep fails, back off and re-sweep
  from the top.

Only :func:`is_transient` errors are retried — a real bug (a 400, a ``KeyError``)
propagates immediately instead of looping. ``sleep`` is injected so tests never
actually wait.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_fixed

from .core import CallProvider

if TYPE_CHECKING:
    from tenacity import RetryCallState

# The per-endpoint attempt count (``m``) and the backoff between full sweeps
# (``round_delays``) are NOT hardcoded here — they come from the `failover:`
# config (``num_retries`` + ``round_backoff_s``), so an operator tunes index-time
# resilience the same way as chat (#196-followup). The caller passes them in.

# A switch/retry notice: (provider we just gave up on, the transient cause).
OnSwitch = Callable[[CallProvider, BaseException], object]

# HTTP statuses that mean "try again shortly", not "your request is wrong".
_TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

# litellm/openai connection + timeout errors may not carry an HTTP status_code.
_TRANSIENT_NAMES = frozenset(
    {"APIConnectionError", "APITimeoutError", "Timeout", "ServiceUnavailableError"}
)


def is_transient(exc: BaseException) -> bool:
    """True when ``exc`` is a retryable transient (a gateway/overload/timeout
    blip), False for a permanent error (a 400, an auth/context error, a real
    bug) that retrying can never fix."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int) and code in _TRANSIENT_STATUS:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


def try_provider[R](
    call: Callable[[], R],
    *,
    m: int,
    gap: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
) -> R:
    """Call ``call`` up to ``m`` times, ``gap`` seconds apart, retrying ONLY a
    transient failure. A permanent error (or the m-th transient) propagates."""
    for attempt in Retrying(
        stop=stop_after_attempt(m),
        wait=wait_fixed(gap),
        retry=retry_if_exception(is_transient),
        reraise=True,
        sleep=sleep,
    ):
        with attempt:
            return call()
    raise AssertionError("unreachable")  # pragma: no cover — Retrying always returns or raises


def _round_wait(delays: Sequence[float]) -> Callable[[RetryCallState], float]:
    """Tenacity wait that walks ``delays`` (1st retry waits delays[0], …) and
    holds the last value for any further round."""

    def wait(state: RetryCallState) -> float:
        return delays[min(state.attempt_number - 1, len(delays) - 1)]

    return wait


def call_with_failover[R](
    providers: Sequence[CallProvider[R]],
    *,
    m: int,
    gap: float = 0.2,
    round_delays: Sequence[float],
    sleep: Callable[[float], None] = time.sleep,
    on_switch: OnSwitch | None = None,
) -> R:
    """Sweep ``providers`` in priority order, switching to the next on a
    transient failure (``try_provider`` gives each ``m`` quick shots). If a whole
    sweep fails transiently, back off (``round_delays``) and re-sweep from the
    top; a permanent error aborts the whole thing immediately."""

    def _sweep() -> R:
        last: BaseException = RuntimeError("call_with_failover: no providers")
        for provider in providers:
            try:
                return try_provider(provider.call, m=m, gap=gap, sleep=sleep)
            except Exception as exc:
                if not is_transient(exc):
                    raise  # a real bug / bad request — don't burn the chain
                if on_switch is not None:
                    on_switch(provider, exc)
                last = exc  # transient — try the next provider
        raise last  # whole sweep failed transiently → let the round retry catch it

    for attempt in Retrying(
        stop=stop_after_attempt(len(round_delays) + 1),
        wait=_round_wait(round_delays),
        retry=retry_if_exception(is_transient),
        reraise=True,
        sleep=sleep,
    ):
        with attempt:
            return _sweep()
    raise AssertionError("unreachable")  # pragma: no cover — Retrying always returns or raises
