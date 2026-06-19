"""#113 end-to-end through ChatTurnEngine: a repetition loop is stopped, the
repeats stream live, but the persisted message is truncated to before the loop.
"""

import asyncio

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import MessageDelta, RepetitionStopped, ToolEnd, ToolStart
from workspace_app.api.turns import ChatTurnEngine, TurnMessage, _TurnReducer


class _LoopyRunner:
    """Emits a clean sentence, then degenerates into a loop forever — the guard
    must stop it (we cap the loop so a broken guard fails instead of hanging)."""

    async def run(self, prompt, ctx):
        yield MessageDelta(text="Good answer. ")
        for _ in range(200):
            yield MessageDelta(text="loopy ")
        yield MessageDelta(text="UNREACHABLE")


def _run_turn(runner) -> tuple[list[TurnMessage], list[str]]:
    captured: list[list[TurnMessage]] = []
    frames: list[str] = []
    engine = ChatTurnEngine(runner)

    async def go() -> None:
        resp = await engine.stream(
            "k", "q", AgentToolContext(), on_complete=lambda p: captured.append(p)
        )
        async for f in resp.body_iterator:
            frames.append(f)

    asyncio.run(go())
    return captured[0], frames


def test_loop_truncates_persisted_message_yet_streams_the_repeats_live():
    produced, frames = _run_turn(_LoopyRunner())

    assistant = next(m for m in produced if m.role == "assistant")
    # Persisted: clean prefix only, loop dropped, flagged for the FE notice.
    assert assistant.content == "Good answer. "
    assert assistant.stopped_reason == "repetition"

    # Live stream (decision "b"): the repeats DID reach the user, plus a marker.
    assert any("loopy" in f for f in frames)
    assert any("repetition_stopped" in f for f in frames)
    # The runner was cut off — text after the loop never streamed.
    assert not any("UNREACHABLE" in f for f in frames)


class _ReasoningLoopRunner:
    """Loops forever in the reasoning channel without ever answering — the
    worst token-burner. Nothing reaches the visible content channel."""

    async def run(self, prompt, ctx):
        yield MessageDelta(text="Let me think about it. ", reasoning=True)
        for _ in range(200):
            yield MessageDelta(text="hmm reconsider ", reasoning=True)


def test_a_reasoning_loop_truncates_reasoning_and_is_flagged():
    produced, frames = _run_turn(_ReasoningLoopRunner())

    assistant = next(m for m in produced if m.role == "assistant")
    assert assistant.stopped_reason == "repetition"
    assert assistant.reasoning == "Let me think about it. "  # loop dropped
    assert assistant.content == ""  # never answered
    assert any('"channel": "reasoning"' in f for f in frames)


def test_truncation_targets_the_assistant_answer_skipping_a_trailing_tool():
    # The marker truncates the assistant answer even when a tool message is the
    # most recent produced entry (reverse-scan past it).
    r = _TurnReducer()
    r.add(MessageDelta(text="answer" + "lo" * 10))  # 6 + 20 chars
    r.add(ToolStart(call_id="c", name="exec", args={}))
    r.add(ToolEnd(call_id="c", output="done"))
    r.add(RepetitionStopped(loop_length=20, channel="content"))

    assistant = next(m for m in r.produced if m.role == "assistant")
    assert assistant.content == "answer"
    assert assistant.stopped_reason == "repetition"


def test_a_stray_marker_with_no_assistant_answer_is_a_harmless_noop():
    r = _TurnReducer()
    r.add(ToolStart(call_id="c", name="exec", args={}))
    r.add(ToolEnd(call_id="c", output="ok"))
    r.add(RepetitionStopped(loop_length=5))
    assert all(m.stopped_reason is None for m in r.produced)


def test_rca_mapper_persists_stopped_reason_so_a_reload_shows_the_notice():
    from workspace_app.api.app import _to_rca_message

    msg = _to_rca_message(
        TurnMessage(role="assistant", content="partial", stopped_reason="repetition")
    )
    assert msg.stopped_reason == "repetition"
