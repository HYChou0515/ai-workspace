"""Bound every LLM stream in time, whether or not failover is configured (#493).

The ttft / idle deadlines lived ONLY inside ``failover.model.FallbackModel``,
which is built only when a preset declares two or more ``fallbacks`` — and
``fallbacks:`` is commented out in ``config.example.yaml``. So on the default
single-endpoint setup nothing bounded the call at all: ``LitellmModel`` is
constructed with no timeout, and if the provider accepted the request and then
went quiet (a busy Ollama is the common case here), the turn waited forever. No
event was emitted, nothing was persisted, and there is no turn watchdog — the
user got a spinner and a climbing counter with no way to learn why.

``DeadlineModel`` is the orthogonal piece: a give-up bound applied to any model,
with none of failover's switching / cooldown / retry semantics. Exceeding it
raises, which the runner already turns into a terminal error event plus a
persisted error message — so the turn ENDS and says so.

The bound here is deliberately NOT ``ttft_timeout_s``. That 8s figure is a
SWITCHING signal — "this endpoint is busy, try the next one" — which is cheap and
recoverable only when there IS a next one. With a single endpoint there is
nowhere to switch, so reusing it would just kill turns for being slow: measured
call latency on this deploy is a 14.7s median and a 28.5s p90, and a long blank
window is usually the provider being busy, not dead. Being slow is not being
dead. What always has to exist is the give-up bound —
``total_deadline_s`` ("caps the whole turn so it fails readably instead of
hanging forever") — so a turn that produced NOTHING for that long ends and says
why, rather than hanging silently for an hour.

The exception types are failover's (``TtftTimeout`` / ``StreamStalled``) on
purpose: a stall means the same thing to everything downstream regardless of
which layer noticed it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

from agents.models.interface import Model

from ..failover.core import StreamStalled, TtftTimeout


class DeadlineModel(Model):
    """Wrap ``inner`` so its stream must produce a first event within
    ``first_event_s`` and never go quiet for longer than ``idle_s``.

    ``first_event_s`` is a GIVE-UP bound, not a switching one — pass the turn
    deadline, never the (much shorter) failover ttft. ``idle_s`` is the genuine
    death signal: output started and then stopped.

    A non-positive bound disables that bound, so an operator can opt out of
    either without the wiring changing shape. A stream that finishes without
    yielding anything is a valid (if useless) success, not a timeout — matching
    ``FallbackModel``.
    """

    def __init__(self, inner: Model, *, first_event_s: float, idle_s: float) -> None:
        self._inner = inner
        self._first_event_s = first_event_s
        self._idle_s = idle_s

    def __getattr__(self, name: str) -> Any:
        # Anything not overridden here (get_response and friends) is the inner
        # model's — the deadline only concerns streaming.
        return getattr(self._inner, name)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.get_response(*args, **kwargs)

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        stream = self._inner.stream_response(*args, **kwargs)
        it = stream.__aiter__()
        try:
            try:
                first = await self._next(it, self._first_event_s)
            except StopAsyncIteration:
                return  # empty stream — a finish, not a stall
            except TimeoutError as exc:
                raise TtftTimeout(f"no output at all within {self._first_event_s}s") from exc
            yield first
            while True:
                try:
                    event = await self._next(it, self._idle_s)
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise StreamStalled(f"stream idle for more than {self._idle_s}s") from exc
                yield event
        except BaseException:
            # The SDK types stream_response as AsyncIterator, but at runtime it is
            # an async generator — close it so an abandoned provider connection is
            # torn down rather than left suspended (same reason FallbackModel does).
            await cast("AsyncGenerator[Any]", stream).aclose()
            raise

    @staticmethod
    async def _next(it: AsyncIterator[Any], timeout: float) -> Any:
        if timeout <= 0:
            return await it.__anext__()
        return await asyncio.wait_for(it.__anext__(), timeout=timeout)
