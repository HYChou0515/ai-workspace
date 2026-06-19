"""guard_repetition — stream middleware that stops a degenerating turn (#113).

Wraps a runner's AgentEvent stream: forwards every event (the repeats stay
live, decision "b"), and the first time the content/reasoning tail degenerates
into a loop it emits a RepetitionStopped marker and stops consuming — which
cancels the underlying generation.
"""

from collections.abc import AsyncIterator

from workspace_app.api.events import (
    MessageDelta,
    RepetitionStopped,
    RunDone,
    ToolEnd,
    ToolStart,
)
from workspace_app.api.repetition_guard import guard_repetition


async def _drain(src: AsyncIterator) -> list:
    return [ev async for ev in guard_repetition(src)]


async def test_guard_stops_a_degenerate_content_stream_and_does_not_drain_it():
    async def src() -> AsyncIterator:
        yield MessageDelta(text="Here is the answer. ")
        for _ in range(100):
            yield MessageDelta(text="loopy ")  # degenerates
        yield MessageDelta(text="SHOULD_NOT_APPEAR")  # past the stop point

    out = await _drain(src())

    assert any(isinstance(e, RepetitionStopped) for e in out)
    assert not any(isinstance(e, MessageDelta) and "SHOULD_NOT_APPEAR" in e.text for e in out)


async def test_a_normal_stream_passes_through_untouched():
    events = [
        MessageDelta(text="First, let me look. "),
        MessageDelta(text="The disk is full; "),
        MessageDelta(text="rotating logs fixes it."),
        RunDone(),
    ]
    out = await _drain(_emit(events))
    assert out == events
    assert not any(isinstance(e, RepetitionStopped) for e in out)


async def _emit(events: list) -> AsyncIterator:
    for ev in events:
        yield ev


async def test_stopping_closes_the_underlying_stream_to_cancel_generation():
    # The whole point: when we stop, the upstream generator is closed promptly
    # (its finally runs) so the LLM stops generating instead of looping on.
    closed: list[bool] = []

    async def src() -> AsyncIterator:
        try:
            yield MessageDelta(text="Here is the answer. ")
            while True:
                yield MessageDelta(text="loopy ")
        finally:
            closed.append(True)

    out = await _drain(src())
    assert any(isinstance(e, RepetitionStopped) for e in out)
    assert closed == [True]


async def test_a_plain_async_iterator_without_aclose_is_handled():
    # The runner Protocol returns AsyncIterator — not necessarily an async
    # generator — so the guard must not assume `aclose` exists.
    class _Iter:
        def __init__(self, items: list) -> None:
            self._it = iter(items)

        def __aiter__(self) -> "_Iter":
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration from None

    out = await _drain(_Iter([MessageDelta(text="all good "), RunDone()]))
    assert not any(isinstance(e, RepetitionStopped) for e in out)


async def test_a_reasoning_channel_loop_is_caught_and_tagged():
    async def src() -> AsyncIterator:
        yield MessageDelta(text="Let me think. ", reasoning=True)
        for _ in range(100):
            yield MessageDelta(text="still thinking ", reasoning=True)

    out = await _drain(src())
    stops = [e for e in out if isinstance(e, RepetitionStopped)]
    assert len(stops) == 1
    assert stops[0].channel == "reasoning"


async def test_content_and_reasoning_are_tracked_independently():
    # Interleaving reasoning chatter must not let unrelated content count toward
    # a content loop (and vice versa).
    async def src() -> AsyncIterator:
        for _ in range(100):
            yield MessageDelta(text="thinking ", reasoning=True)
            yield MessageDelta(text=f"point {_} ", reasoning=False)

    out = await _drain(src())
    stops = [e for e in out if isinstance(e, RepetitionStopped)]
    assert stops and stops[0].channel == "reasoning"


async def test_a_tool_call_boundary_resets_detection_so_cross_step_repeats_pass():
    block = "讓我檢查foo,現在有遇到問題xxx,"  # > min loop chars
    events = [
        MessageDelta(text=block),
        MessageDelta(text=block),  # 2× before the tool call
        ToolStart(call_id="c1", name="exec", args={}),
        ToolEnd(call_id="c1", output="ok"),
        MessageDelta(text=block),
        MessageDelta(text=block),  # 2× after — never 3× in a row within one response
        RunDone(),
    ]
    out = await _drain(_emit(events))
    assert not any(isinstance(e, RepetitionStopped) for e in out)
