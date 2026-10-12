"""docs/plan-friendly-errors.md D2/D3 — a failed model call is retried AS THAT
CALL, never by re-running the whole turn.

The turn loop used to restart the entire agent run on a failure, which would
have repeated every tool already executed — so it refused once anything had
streamed, and a 429 or a dropped connection in the middle of an agentic turn
could only end the turn with the provider's raw string."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import litellm
import pytest
from agents.models.interface import Model

from workspace_app.agent.call_retry_model import (
    CallRetracted,
    CallRetryModel,
    CallStarted,
    ModelCallFailed,
)


def _resp(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "http://llm.test/v1")
    )


def _unavailable() -> Exception:
    return litellm.ServiceUnavailableError("down", "openai", "m", response=_resp(503))


def _rate_limited(retry_after: str | None = None) -> Exception:
    headers = {"retry-after": retry_after} if retry_after else {}
    return litellm.RateLimitError("slow down", "openai", "m", response=_resp(429, headers))


@dataclass
class _Ev:
    """A provider stream event — only `type` matters to the wrapper."""

    type: str
    delta: str = ""


DELTA = "response.output_text.delta"
DONE_ITEM = "response.output_item.done"


class _Scripted(Model):
    """Each call plays the next attempt: events, then optionally a raise."""

    def __init__(self, attempts: list[tuple[list[Any], Exception | None]]) -> None:
        self.attempts = list(attempts)
        self.inputs: list[Any] = []

    async def stream_response(self, system_instructions: Any, input: Any, *a: Any, **k: Any):
        self.inputs.append(input)
        events, exc = self.attempts.pop(0)
        for ev in events:
            yield ev
        if exc is not None:
            raise exc

    async def get_response(self, system_instructions: Any, input: Any, *a: Any, **k: Any) -> Any:
        self.inputs.append(input)
        events, exc = self.attempts.pop(0)
        if exc is not None:
            raise exc
        return events


class _Notes:
    def __init__(self) -> None:
        self.retries: list[tuple[int, str]] = []
        self.holds: list[float] = []
        self.slept: list[float] = []
        #: Every hold and sleep, in the order they happened.
        self.timeline: list[str] = []

    def on_retry(self, attempt: int, kind: str) -> None:
        self.retries.append((attempt, kind))

    def on_hold(self, seconds: float) -> None:
        self.holds.append(seconds)
        self.timeline.append(f"held:{seconds}")

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.timeline.append(f"slept:{seconds}")


def _model(inner: Model, notes: _Notes, **kw: Any) -> CallRetryModel:
    return CallRetryModel(
        inner,
        max_retries=kw.pop("max_retries", 2),
        rate_limit_budget_s=kw.pop("rate_limit_budget_s", 120.0),
        on_retry=notes.on_retry,
        on_hold=notes.on_hold,
        sleep=notes.sleep,
        **kw,
    )


async def _drain(model: Any, input: Any = "in") -> list[Any]:
    return [ev async for ev in model.stream_response(None, input, None, [], None, [], None)]


def _visible(events: list[Any]) -> list[Any]:
    return [e for e in events if not isinstance(e, (CallStarted, CallRetracted))]


@pytest.mark.asyncio
async def test_a_transient_failure_is_resent_as_the_same_call() -> None:
    notes = _Notes()
    inner = _Scripted([([], _unavailable()), ([_Ev(DELTA, "hi")], None)])

    events = await _drain(_model(inner, notes))

    assert _visible(events) == [_Ev(DELTA, "hi")]
    assert inner.inputs == ["in", "in"], "the SAME request, not a re-run of anything else"
    assert notes.retries == [(1, "provider_error")]


@pytest.mark.asyncio
async def test_a_call_that_broke_mid_answer_is_withdrawn_then_resent() -> None:
    """D3: the partial text of THIS call is withdrawn — the retracted marker
    rides the stream between the two attempts, so whatever counts what an
    attempt showed sees it exactly where the first attempt's text ends."""
    notes = _Notes()
    inner = _Scripted([([_Ev(DELTA, "half an ans")], _unavailable()), ([_Ev(DELTA, "all")], None)])

    events = await _drain(_model(inner, notes))

    assert events == [
        CallStarted(),
        _Ev(DELTA, "half an ans"),
        CallRetracted(),
        CallStarted(),
        _Ev(DELTA, "all"),
    ]


@pytest.mark.asyncio
async def test_a_call_that_never_recovers_is_given_up_with_the_count() -> None:
    """The copy says how many times it tried (D5 `timeout`), and the kind is
    still read off the cause."""
    from workspace_app.failover.classify import classify_error

    notes = _Notes()
    inner = _Scripted([([], TimeoutError("slow"))] * 3)

    with pytest.raises(ModelCallFailed) as raised:
        await _drain(_model(inner, notes, max_retries=2))

    assert raised.value.attempts == 3
    assert classify_error(raised.value) == "timeout"
    assert notes.retries == [(1, "timeout"), (2, "timeout")]


@pytest.mark.asyncio
async def test_a_request_that_is_wrong_is_not_resent() -> None:
    """A 400 / a refused credential fails identically however often it is sent;
    re-sending it is only latency before the same answer."""
    notes = _Notes()
    bad = litellm.AuthenticationError("bad key", "openai", "m", response=_resp(401))
    inner = _Scripted([([], bad)])

    with pytest.raises(litellm.AuthenticationError):
        await _drain(_model(inner, notes))

    assert notes.retries == []


@pytest.mark.asyncio
async def test_a_call_whose_items_the_sdk_already_recorded_is_not_resent() -> None:
    """The SDK records each `response.output_item.done` item as it arrives (and
    may announce a tool call from it). A re-send would then add a second copy
    of everything, so past that point the failure is the call's answer."""
    notes = _Notes()
    inner = _Scripted([([_Ev(DONE_ITEM)], _unavailable()), ([_Ev(DELTA, "never")], None)])

    with pytest.raises(litellm.ServiceUnavailableError):
        await _drain(_model(inner, notes))

    assert notes.retries == []


@pytest.mark.asyncio
async def test_a_rate_limit_is_waited_out_for_the_window_it_states() -> None:
    notes = _Notes()
    inner = _Scripted([([], _rate_limited("5")), ([_Ev(DELTA, "ok")], None)])

    events = await _drain(_model(inner, notes))

    assert _visible(events) == [_Ev(DELTA, "ok")]
    assert notes.holds == [5.0], "a held call must SAY it is held (#742)"
    assert notes.slept == [5.0]


@pytest.mark.asyncio
async def test_a_rate_limit_does_not_spend_the_retry_budget() -> None:
    """`max_retries` is for a provider blip — a different failure on a
    different timescale. Charging holds to it gives up on a limit that clears
    on the third window because two blips' worth of attempts was all there was."""
    notes = _Notes()
    inner = _Scripted([([], _rate_limited("5"))] * 3 + [([_Ev(DELTA, "ok")], None)])

    events = await _drain(_model(inner, notes, max_retries=2))

    assert _visible(events) == [_Ev(DELTA, "ok")]
    assert notes.slept == [5.0, 5.0, 5.0]


@pytest.mark.asyncio
async def test_the_hold_is_announced_before_the_call_goes_quiet() -> None:
    """A minute of silence reads as a hung turn; the notice must arrive BEFORE
    the wait, or it explains a silence that is already over (#742)."""
    notes = _Notes()
    inner = _Scripted([([], _rate_limited("5")), ([_Ev(DELTA, "ok")], None)])

    await _drain(_model(inner, notes))

    assert notes.timeline == ["held:5.0", "slept:5.0"]


@pytest.mark.asyncio
async def test_a_whole_response_call_is_retried_the_same_way() -> None:
    """The non-streamed path (WORKSPACE_AGENT_STREAM=0) shares the decision."""
    notes = _Notes()
    inner = _Scripted([([], _unavailable()), (["answer"], None)])

    out = await _model(inner, notes).get_response(None, "in", None, [], None, [], None)

    assert out == ["answer"]
    assert notes.retries == [(1, "provider_error")]


@pytest.mark.asyncio
async def test_a_rate_limit_that_outlasts_the_budget_is_given_up_with_the_wait() -> None:
    """D4 keeps the budget; D5's copy says how long it waited."""
    from workspace_app.failover.classify import classify_error

    notes = _Notes()
    inner = _Scripted([([], _rate_limited("50")), ([], _rate_limited("50"))])

    with pytest.raises(ModelCallFailed) as raised:
        await _drain(_model(inner, notes, rate_limit_budget_s=60.0))

    assert notes.slept == [50.0], "the second wait would exceed the budget"
    assert raised.value.waited_s == 50.0
    assert classify_error(raised.value) == "rate_limited"


def _m(content: str) -> dict[str, str]:
    """An SDK input item — what the provider is actually sent."""
    return {"role": "user", "content": content}


def _too_long() -> Exception:
    return litellm.ContextWindowExceededError(
        "This model's maximum context length is 8192 tokens", "m", "openai", response=_resp(400)
    )


class _DropFirst:
    """A trim that drops the oldest item each time it is asked."""

    def __init__(self, *, refuse: bool = False, no_smaller: bool = False) -> None:
        self.refuse = refuse
        self.no_smaller = no_smaller
        self.asked = 0

    def apply(self, input: Any) -> Any:
        return input

    def shrink(self, input: list[Any], exc: BaseException) -> list[Any] | None:
        self.asked += 1
        if self.refuse:
            return None
        if self.no_smaller:
            return list(input)
        return input[1:]


@pytest.mark.asyncio
async def test_a_request_too_long_is_resent_smaller() -> None:
    """#624, at the call: an over-long request is the one failure with a
    productive answer — send less."""
    notes = _Notes()
    inner = _Scripted([([], _too_long()), ([_Ev(DELTA, "ok")], None)])
    trim = _DropFirst()

    events = await _drain(_model(inner, notes, trim=trim), input=[_m("a" * 400), _m("b"), _m("go")])

    assert _visible(events) == [_Ev(DELTA, "ok")]
    assert inner.inputs == [[_m("a" * 400), _m("b"), _m("go")], [_m("b"), _m("go")]]


@pytest.mark.asyncio
async def test_a_request_that_cannot_be_made_smaller_is_the_answer() -> None:
    notes = _Notes()
    inner = _Scripted([([], _too_long())])

    with pytest.raises(litellm.ContextWindowExceededError):
        await _drain(_model(inner, notes, trim=_DropFirst(refuse=True)), input=[_m("a"), _m("go")])


@pytest.mark.asyncio
async def test_a_trim_that_does_not_shrink_stops_instead_of_spinning() -> None:
    """The overflow path bypasses the attempt count, so its termination rests
    ENTIRELY on every round being smaller — checked here, not trusted to the
    trim: when the guarantee lapsed before, the suite simply never finished."""
    notes = _Notes()
    inner = _Scripted([([], _too_long())] * 5)
    trim = _DropFirst(no_smaller=True)

    with pytest.raises(litellm.ContextWindowExceededError):
        await _drain(_model(inner, notes, trim=trim), input=[_m("a" * 400), _m("go")])

    assert trim.asked == 1
