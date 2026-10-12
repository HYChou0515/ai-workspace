"""Retry a failed model call AS THAT CALL (docs/plan-friendly-errors.md D2/D3).

The turn loop used to restart the whole agent run on a failure. That repeats
every tool the turn already executed, so it refused once anything had streamed
(#26) — and a 429, a dropped connection or a 5xx in the middle of an agentic
turn (the second model call, after a tool ran) could only end the turn with the
provider's raw string. Retrying the one request that failed has none of that
cost: the tools already run stay run, and only this call is sent again.

Not the SDK's own ``ModelSettings.retry``: it refuses to retry once any stream
event has been emitted (``_stream_event_blocks_retry``), which is exactly the
mid-answer case this exists for, and it has no hook to show a hold to the user.
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from agents import ModelSettings
from agents.models.interface import Model

from ..context_budget import estimate_messages
from ..failover.classify import ModelCallFailed, classify_error
from ..failover.rate_limit import is_rate_limited, rate_limit_wait_s

_ITEM_DONE = "response.output_item.done"

#: Kinds a re-send of the SAME request can cure: the provider (or the path to
#: it) was unwell for a moment. Everything else — a bad request, a refused
#: credential — fails identically however often it is sent.
_TRANSIENT = frozenset({"timeout", "connection", "provider_error", "all_busy"})

CALL_STARTED = "workspace.call_started"
CALL_RETRACTED = "workspace.call_retracted"


@dataclass(frozen=True)
class CallStarted:
    """In-stream marker: an attempt at this call begins. Rides the SAME stream
    as the deltas — the SDK forwards any event it does not recognise as a raw
    event — so whatever counts what an attempt showed counts from here, with
    nothing still in flight to overtake it."""

    type: str = CALL_STARTED


@dataclass(frozen=True)
class CallRetracted:
    """In-stream marker: the attempt since the last :class:`CallStarted` failed
    and will be re-sent — what it showed is to be withdrawn (D3)."""

    type: str = CALL_RETRACTED


class InputTrim(ABC):
    """How a call that was too long is made smaller (#624). The owner knows
    which part of the input may be given up — the conversation BEFORE this
    turn; this turn's own tool calls and results must travel together."""

    @abstractmethod
    def apply(self, input: Any) -> Any:
        """The input to send, with whatever earlier rejections already cut."""

    @abstractmethod
    def shrink(self, input: Any, exc: BaseException) -> Any | None:
        """A smaller input after ``exc`` rejected ``input``, or None when
        nothing more can be given up."""


class _Attempts:
    """One call's retry bookkeeping, shared by both shapes of a call.

    The re-send COUNT is per call, deliberately: it bounds one call's blips,
    and a long agentic turn that weathered two early ones must not end on the
    first blip of a later call (the turn's length is bounded by max_turns). The
    rate-limit HOLDS are the turn's — see `CallRetryModel`."""

    def __init__(self, owner: CallRetryModel) -> None:
        self._owner = owner
        self.retries = 0
        self.holds = 0

    def _given_up(self, exc: BaseException) -> ModelCallFailed:
        return ModelCallFailed(
            attempts=self.retries + self.holds + 1, waited_s=self._owner._waited_s
        )

    async def next_input(self, input: Any, exc: Exception) -> Any:
        """The input to re-send after ``exc``, having waited if that is the
        cure — or raise: the failure is this call's answer."""
        owner = self._owner
        if is_rate_limited(exc):
            held = rate_limit_wait_s(exc, attempt=self.holds + 1)
            if owner._waited_s + held > owner._rate_limit_budget_s:
                raise self._given_up(exc) from exc
            self.holds += 1
            owner._waited_s += held
            if owner._on_hold is not None:
                owner._on_hold(held)
            await owner._sleep(held)
            return input
        kind = classify_error(exc)
        if kind == "context_overflow":
            # Deliberately outside the attempt count — a fixed budget would
            # strand a long thread — so termination rests ENTIRELY on every
            # round being smaller. That is checked HERE, not trusted to the
            # trim: when the guarantee lapsed, the suite simply never finished.
            smaller = owner._trim.shrink(input, exc) if owner._trim is not None else None
            if smaller is None or estimate_messages(smaller) >= estimate_messages(input):
                raise exc
            return smaller
        if kind not in _TRANSIENT:
            raise exc
        if self.retries >= owner._max_retries:
            raise self._given_up(exc) from exc
        self.retries += 1
        if owner._on_retry is not None:
            owner._on_retry(self.retries, kind)
        return input


# This wrapper is the ONE retry layer only if the client under it does not
# retry too: litellm builds its OpenAI client with `max_retries=2`, which waits
# out a short Retry-After and re-sends on its own, so a 429 never reached here —
# no hold shown, no count, no budget charged (found recording the #915 demo).
_PROVIDER_RETRIES_OFF = ModelSettings(extra_args={"max_retries": 0, "num_retries": 0})


def _provider_retries_off(args: tuple[Any, ...]) -> tuple[Any, ...]:
    """`model_settings` is the third parameter of `Model.get_response` /
    `stream_response`; the SDK passes it positionally. `resolve` merges
    `extra_args`, so the call's own arguments (`num_ctx`, `think`) survive."""
    return (_off(args[0]), *args[1:])


def _off(settings: ModelSettings | None) -> ModelSettings:
    return (settings or ModelSettings()).resolve(_PROVIDER_RETRIES_OFF)


class CallRetryModel(Model):
    def __init__(
        self,
        inner: Model,
        *,
        max_retries: int,
        rate_limit_budget_s: float,
        on_retry: Callable[[int, str], None] | None = None,
        on_hold: Callable[[float], None] | None = None,
        trim: InputTrim | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._rate_limit_budget_s = rate_limit_budget_s
        self._on_retry = on_retry
        self._on_hold = on_hold
        self._trim = trim
        self._sleep = sleep
        # Rate-limit holds are charged to their OWN budget, in seconds, not to
        # `max_retries`: that count exists for a provider blip, a different
        # failure on a different timescale, and spending it on a limit gives up
        # on one that would have cleared. The budget is a POOL for this
        # instance — one per `run()` (`_call_retry`); a delegated sub-agent's run
        # has its own — never a per-call allowance: that reset every tool round,
        # so a turn of N calls could wait N times the budget (review round 1).
        self._waited_s = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _first_input(self, input: Any) -> Any:
        return self._trim.apply(input) if self._trim is not None else input

    async def get_response(
        self, system_instructions: Any, input: Any, *args: Any, **kwargs: Any
    ) -> Any:
        args = _provider_retries_off(args)
        attempts = _Attempts(self)
        input = self._first_input(input)
        while True:
            try:
                return await self._inner.get_response(system_instructions, input, *args, **kwargs)
            except Exception as exc:
                input = await attempts.next_input(input, exc)

    async def stream_response(
        self, system_instructions: Any, input: Any, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        args = _provider_retries_off(args)
        attempts = _Attempts(self)
        input = self._first_input(input)
        while True:
            yield CallStarted()
            recorded = False
            try:
                # `aclosing`: a consumer that stops (a Stop mid-answer) closes
                # the stream this wrapper iterates. The wrappers below it
                # (RepairingModel, HeaderModel, DeadlineModel) re-yield, so the
                # provider's own stream is finalized by the loop's asyncgen
                # hooks shortly after rather than here (review round 2).
                # The SDK types it an AsyncIterator; at runtime it is an async
                # generator (DeadlineModel relies on the same).
                inner = cast(
                    "AsyncGenerator[Any]",
                    self._inner.stream_response(system_instructions, input, *args, **kwargs),
                )
                async with contextlib.aclosing(inner) as stream:
                    async for event in stream:
                        if getattr(event, "type", None) == _ITEM_DONE:
                            recorded = True
                        yield event
                return
            except Exception as exc:
                if recorded:
                    # The SDK has already recorded this attempt's items (and may
                    # have announced a tool call from one); a re-send would add
                    # a second copy of everything.
                    raise
                input = await attempts.next_input(input, exc)
                yield CallRetracted()
