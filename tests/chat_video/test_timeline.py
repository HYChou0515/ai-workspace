"""A chat export becomes a timeline of animation steps (`docs/plan-chat-video.md` P1).

The timeline is the pure middle of the pipeline: it takes exactly what
`build_chat_export` writes (`{title, messages}`, each message a `Message` as
`to_builtins` renders it) and decides what the player will DO — type, stream,
show a tool card — and roughly how long each step takes. No browser, no ffmpeg,
so it is what CI tests; the recorder downstream only plays what this says.
"""

from __future__ import annotations

from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.timeline import (
    CHAR_OVERHEAD_MS,
    PACING,
    ErrorStep,
    NoteStep,
    StreamStep,
    ToolStep,
    TypeStep,
    build_timeline,
)


def _streamed(step: object) -> tuple[str, bool]:
    assert isinstance(step, StreamStep), step
    return step.text, step.reasoning


def test_a_user_message_is_typed_into_the_composer_by_its_author():
    steps = build_timeline(
        title="t",
        messages=[{"role": "user", "content": "把 OOM 前 30 秒畫出來", "author": "hychou"}],
        options=VideoOptions(),
    ).steps

    assert steps == [TypeStep(author="hychou", text="把 OOM 前 30 秒畫出來", ms=steps[0].ms)]
    assert steps[0].ms > 0


def test_an_assistant_reply_streams_its_reasoning_before_its_answer():
    """The model thought, then answered — the thread shows them in that order
    and so must the animation. A reply with no reasoning gets no thinking
    block at all, not an empty one."""
    opts = VideoOptions()
    steps = build_timeline(
        title="t",
        messages=[
            {"role": "assistant", "content": "答案", "reasoning": "先想一下", "author": "AI"},
            {"role": "assistant", "content": "再答", "author": "AI"},
        ],
        options=opts,
    ).steps

    assert [_streamed(s) for s in steps] == [("先想一下", True), ("答案", False), ("再答", False)]


def test_a_reply_that_only_thought_has_no_empty_answer_step():
    """A turn that ended in a tool call carries reasoning and an empty
    `content`. The thinking streams; an empty answer bubble does not."""
    steps = build_timeline(
        title="t",
        messages=[{"role": "assistant", "content": "", "reasoning": "去讀檔", "author": "AI"}],
        options=VideoOptions(),
    ).steps

    assert [_streamed(s) for s in steps] == [("去讀檔", True)]


def test_a_tool_message_is_a_card_that_spins_then_shows_a_bounded_output():
    """The card shows the call (name + args), spins for the pause, then opens
    the output. A tool that dumped a whole file is cut with an ellipsis: the
    video is for watching, and the cap is the option, not a constant."""
    opts = VideoOptions(tool_output_chars=10, tool_pause_ms=700)
    steps = build_timeline(
        title="t",
        messages=[
            {
                "role": "tool",
                "tool_name": "read_file",
                "tool_args": {"path": "/a.py"},
                "tool_call_id": "c1",
                "content": "0123456789ABCDEF",
            }
        ],
        options=opts,
    ).steps

    assert steps == [
        ToolStep(
            name="read_file",
            args={"path": "/a.py"},
            output="0123456789…",
            ms=700 + PACING["after_tool_ms"],  # the spin, then the card is read
        )
    ]


def test_an_error_is_a_red_bubble_and_every_other_role_is_a_quiet_note():
    """A hand-edited file may carry roles the thread has (`system`, `mention`,
    `goal`) or a typo. None of those is somebody typing: they become a dim
    one-liner, never a `TypeStep` that would zoom the camera in on nothing."""
    steps = build_timeline(
        title="t",
        messages=[
            {"role": "error", "content": "the model timed out", "error_kind": "timeout"},
            {"role": "system", "content": "context reduced"},
            {"role": "mention", "content": "@alice come look", "mentions": ["alice"]},
            {"role": "whatever", "content": "??"},
        ],
        options=VideoOptions(),
    ).steps

    assert steps == [
        ErrorStep(text="the model timed out", kind="timeout", ms=steps[0].ms),
        NoteStep(text="context reduced", ms=steps[1].ms),
        NoteStep(text="@alice come look", ms=steps[2].ms),
        NoteStep(text="??", ms=steps[3].ms),
    ]
    assert not any(isinstance(s, TypeStep) for s in steps)


# ─── the bound: a transcript never becomes a twenty-minute job ────────────────


def _long_chat(n: int) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 100, "author": "a"}
        for i in range(n)
    ]


def test_the_estimate_counts_every_pause_the_player_takes():
    """Derived from `PACING`, the one table the player reads too: a user turn
    costs its typing (punctuation held longer), the camera in and out with
    its settle, and the pause after send; a reply costs its streaming and
    the pause after; a tool costs its spin and the pause after; the whole
    thing has a lead-in and a tail. A recording that ran twice the estimate
    is what this replaced — the ceiling was a lie."""
    opts = VideoOptions(type_ms=10, stream_ms=5, zoom_ms=300, tool_pause_ms=400)
    tl = build_timeline(
        title="t",
        messages=[
            {"role": "user", "content": "ab,", "author": "u"},
            {"role": "assistant", "content": "abcd", "author": "AI"},
            {"role": "tool", "tool_name": "x", "content": "o"},
        ],
        options=opts,
    )

    typing = 3 * 10 + 10 * PACING["punct_factor"]  # three chars, one of them punctuation
    camera = 2 * (300 + PACING["zoom_settle_ms"])
    after_send = PACING["after_type_ms"]
    streaming = 4 * 5 + PACING["after_answer_ms"]
    tool = 400 + PACING["after_tool_ms"]
    edges = PACING["lead_ms"] + PACING["tail_ms"]
    browser = (3 + 4) * CHAR_OVERHEAD_MS  # every character the player inserts
    assert tl.overhead_ms == browser
    assert tl.estimated_ms == typing + camera + after_send + streaming + tool + edges + browser
    assert tl.time_scale == 1.0


def test_speed_divides_and_max_seconds_compresses_the_rest():
    """`speed=2` halves everything. Whatever is still over `max_seconds` after
    that is squeezed uniformly, so the animation lands ON the ceiling — not
    truncated, not skipped, every message still shown."""
    fast = build_timeline(title="t", messages=_long_chat(4), options=VideoOptions(speed=2.0))
    slow = build_timeline(title="t", messages=_long_chat(4), options=VideoOptions(speed=1.0))
    # `speed` halves what the player asks for; the browser's own cost stays.
    assert fast.overhead_ms == slow.overhead_ms
    assert (fast.estimated_ms - fast.overhead_ms) * 2 == slow.estimated_ms - slow.overhead_ms

    long = build_timeline(title="t", messages=_long_chat(40), options=VideoOptions(max_seconds=60))
    assert long.estimated_ms > 60_000
    assert 0.05 < long.time_scale < 1
    asked = long.estimated_ms - long.overhead_ms
    assert round(asked * long.time_scale) + long.overhead_ms == 60_000  # lands ON the ceiling
    assert len(long.steps) == 40  # nothing dropped


def test_a_transcript_whose_browser_cost_alone_exceeds_the_ceiling_still_plays():
    """Nothing can squeeze the per-character cost. The scale bottoms out
    rather than going to zero or negative — the video runs long, and the
    recorder's timeout is the honest last word, not a frozen player."""
    long = build_timeline(title="t", messages=_long_chat(400), options=VideoOptions(max_seconds=1))

    assert long.overhead_ms > 1_000
    assert long.time_scale == 0.05
