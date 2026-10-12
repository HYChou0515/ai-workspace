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


@pytest.mark.asyncio
async def test_the_rate_limit_budget_is_the_turns_not_each_calls() -> None:
    """Review round 1: one budget per TURN (one CallRetryModel per agent run).
    A fresh allowance per call reset every tool round, so a turn of N calls
    could wait N times the budget — exactly what the budget exists to bound."""
    notes = _Notes()
    inner = _Scripted(
        [([], _rate_limited("50")), ([_Ev(DELTA, "a")], None), ([], _rate_limited("50"))]
    )
    model = _model(inner, notes, rate_limit_budget_s=60.0)

    await _drain(model)  # the first call waits 50 s of the turn's 60
    with pytest.raises(ModelCallFailed) as raised:
        await _drain(model)  # the second cannot fit another 50

    assert notes.slept == [50.0]
    assert raised.value.waited_s == 50.0


@pytest.mark.asyncio
async def test_a_blip_late_in_the_turn_is_not_charged_against_an_earlier_one() -> None:
    """The re-send COUNT stays per call, deliberately: it bounds one call's
    blips, and a long agentic turn that weathered two early ones must not end on
    the first blip of its tenth call. The turn's length is bounded by max_turns."""
    notes = _Notes()
    inner = _Scripted(
        [
            ([], _unavailable()),
            ([], _unavailable()),
            ([_Ev(DELTA, "a")], None),
            ([], _unavailable()),
            ([_Ev(DELTA, "b")], None),
        ]
    )
    model = _model(inner, notes, max_retries=2)

    await _drain(model)
    events = await _drain(model)

    assert _visible(events) == [_Ev(DELTA, "b")]


@pytest.mark.asyncio
async def test_a_consumer_that_stops_closes_the_providers_stream() -> None:
    """A Stop mid-answer closes the outer stream; the inner one is the HTTP
    stream to the provider and must be closed with it, not left to GC."""
    closed: list[bool] = []

    class _Endless(Model):
        async def get_response(self, *a: Any, **k: Any) -> Any:  # pragma: no cover
            raise NotImplementedError

        async def stream_response(self, *a: Any, **k: Any):
            try:
                while True:
                    yield _Ev(DELTA, "x")
            finally:
                closed.append(True)

    stream: Any = _model(_Endless(), _Notes()).stream_response(None, "in", None, [], None, [], None)
    await stream.__anext__()  # CallStarted
    await stream.__anext__()  # the first delta
    await stream.aclose()

    assert closed == [True]


async def test_turning_the_providers_retries_off_keeps_the_calls_own_arguments():
    """The off switch is merged into the call's settings, not swapped in for
    them: Ollama's `num_ctx` rides the same `extra_args`, and losing it would
    shrink the window the turn was planned for."""
    from agents import ModelSettings

    seen: list[Any] = []

    class _Records(_Scripted):
        async def stream_response(self, system_instructions: Any, input: Any, *a: Any, **k: Any):
            seen.append(a[0])
            async for ev in super().stream_response(system_instructions, input, *a, **k):
                yield ev

    model = _model(_Records([([_Ev(DELTA, "ok")], None)]), _Notes())
    settings = ModelSettings(extra_args={"num_ctx": 8192})

    _ = [ev async for ev in model.stream_response(None, "in", settings, [], None, [], None)]

    assert [s.extra_args for s in seen] == [{"num_ctx": 8192, "max_retries": 0, "num_retries": 0}]


@pytest.mark.asyncio
async def test_a_transient_failure_backs_off_before_each_resend() -> None:
    """Review round 3: turning the provider client's retries off also turned
    off its backoff (0.5 s, doubling, at most 8 s), so a gateway that answers
    503 for a second got every re-send within milliseconds and the turn ended.
    The re-send waits the same way the client did — announced first, like a
    hold, so the status line says why the call went quiet."""
    notes = _Notes()
    inner = _Scripted([([], _unavailable()), ([], _unavailable()), ([_Ev(DELTA, "hi")], None)])

    events = await _drain(_model(inner, notes, max_retries=5))

    assert _visible(events) == [_Ev(DELTA, "hi")]
    assert notes.slept == [0.5, 1.0]
    assert notes.retries == [(1, "provider_error"), (2, "provider_error")]
