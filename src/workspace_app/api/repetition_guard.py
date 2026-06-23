"""Repetition guard — stream middleware over a turn's AgentEvent stream (#113).

A model can degenerate into a repetition loop mid-response and never converge,
burning tokens and stalling the turn (neural text degeneration). No backend
penalty reliably stops it, so we watch the streamed tail ourselves.

`guard_repetition` wraps the runner's event stream and forwards every event
unchanged — the repeats stay live so the user sees the model misbehaved
(decision "b"). The first time the content (or reasoning) tail loops, it emits
a `RepetitionStopped` marker carrying the trailing `loop_length`, then a
`RunDone`, then stops iterating. Stopping closes the wrapped generator, whose
`finally` cancels the in-flight LLM generation. The persisted-message
truncation is applied downstream by the turn reducer from the same marker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..agent.repetition import RepetitionDetector
from .events import AgentEvent, MessageDelta, RepetitionStopped, RunDone, ToolStart


async def guard_repetition(
    events: AsyncIterator[AgentEvent],
    *,
    repeats: int = 10,
    max_period: int = 800,
    window: int = 10000,
    min_loop_chars: int = 1200,
) -> AsyncIterator[AgentEvent]:
    # Defaults mirror RepetitionDetector's: this is a deliberately loose,
    # last-resort backstop (#146). A block must repeat `repeats` times AND span
    # `min_loop_chars` before we believe it's runaway degeneration rather than
    # bounded structure (a wide table row, a list) the model will move on from.
    #
    # Content and reasoning loop independently — the reasoning channel is the
    # worst token-burner (a model can loop forever in <think> without ever
    # emitting an answer), so it gets its own detector.
    def _detector() -> RepetitionDetector:
        return RepetitionDetector(
            repeats=repeats, max_period=max_period, window=window, min_loop_chars=min_loop_chars
        )

    detectors = {"content": _detector(), "reasoning": _detector()}
    # Close the upstream generator on ANY exit (including stopping early), so
    # the in-flight LLM generation is actually cancelled (its `finally` cancels
    # the produce task) instead of looping on until GC. The runner Protocol
    # types this as an AsyncIterator, so close defensively if it supports it.
    try:
        async for ev in events:
            yield ev  # forward unchanged — repeats stay live
            if isinstance(ev, ToolStart):
                # A tool call ends the current text response; the next deltas are
                # a fresh response. Reset so a block repeated once per step
                # (cross-step repetition, case 2) never accumulates into a
                # within-response loop.
                for det in detectors.values():
                    det.reset()
            elif isinstance(ev, MessageDelta):
                channel = "reasoning" if ev.reasoning else "content"
                hit = detectors[channel].feed(ev.text)
                if hit is not None:
                    yield RepetitionStopped(loop_length=hit.loop_length, channel=channel)
                    yield RunDone()
                    return
    finally:
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()
