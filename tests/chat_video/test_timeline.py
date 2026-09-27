"""A chat export becomes a timeline of animation steps (`docs/plan-chat-video.md` P1).

The timeline is the pure middle of the pipeline: it takes exactly what
`build_chat_export` writes (`{title, messages}`, each message a `Message` as
`to_builtins` renders it) and decides what the player will DO — type, stream,
show a tool card — and roughly how long each step takes. No browser, no ffmpeg,
so it is what CI tests; the recorder downstream only plays what this says.
"""

from __future__ import annotations

import json

from workspace_app.agent.shown_files import declare_shown_files
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.timeline import (
    CHAR_OVERHEAD_MS,
    PACING,
    ErrorStep,
    NoteStep,
    ShownFile,
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


def test_a_stopped_reply_carries_why_it_stopped():
    """#113: an answer cut off mid-stream (`stopped_reason`) shows a small
    label saying so — the thread does, and a video that showed a clean stop
    would misreport the run."""
    steps = build_timeline(
        title="t",
        messages=[
            {"role": "assistant", "content": "loop loop", "stopped_reason": "repetition"},
            {"role": "assistant", "content": "fine"},
        ],
        options=VideoOptions(),
    ).steps

    assert [s.stopped for s in steps if isinstance(s, StreamStep)] == ["repetition", ""]


def test_the_playback_estimate_is_the_squeezed_one():
    """Two numbers, both honest: `estimated_ms` is what the transcript asks
    for, `playback_ms` is what the recording will run once the ceiling has
    squeezed it — the one a person is told, and the one the recorder's
    deadline is set from."""
    tl = build_timeline(title="t", messages=_long_chat(40), options=VideoOptions(max_seconds=60))

    assert (
        tl.playback_ms == round((tl.estimated_ms - tl.overhead_ms) * tl.time_scale) + tl.overhead_ms
    )
    assert tl.playback_ms == 60_000
    short = build_timeline(title="t", messages=_long_chat(2), options=VideoOptions())
    assert short.playback_ms == short.estimated_ms  # nothing to squeeze


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
            files=[],
            card=True,
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
    rather than going to zero or negative — the video runs long (its
    `playback_ms` says how long), the recorder's deadline is set from that,
    and a page that still hangs fails by name rather than freezing."""
    long = build_timeline(title="t", messages=_long_chat(400), options=VideoOptions(max_seconds=1))

    assert long.overhead_ms > 1_000
    assert long.time_scale == 0.05


# ─── files a tool put in front of the user ────────────────────────────────────


def test_a_tool_that_declared_files_carries_them_and_shows_the_body_without_the_marker():
    """The FE's own rule (`renderers/shownFiles.ts`): a `[shown-files]` line at
    the end of any tool's result names files to render; `path`, `mime` and
    `size` are required, `caption` optional, a malformed entry is skipped and
    the rest kept. The card's body is the result WITHOUT that line — the
    marker in a card is a glitch the viewer sees."""
    content = declare_shown_files(
        "plotted 5 windows",
        [
            {"path": "/plots/oom.png", "mime": "image/png", "size": 1234, "caption": "五次 OOM"},
            {"path": "/notes.md", "mime": "text/markdown", "size": 88},
            {"path": "", "mime": "image/png", "size": 1},  # malformed: skipped
            {"path": "/x.png", "mime": "image/png"},  # no size: skipped
        ],
    )
    steps = build_timeline(
        title="t",
        messages=[{"role": "tool", "tool_name": "sci_plot", "content": content}],
        options=VideoOptions(),
    ).steps

    assert steps == [
        ToolStep(
            name="sci_plot",
            args={},
            output="plotted 5 windows",
            files=[
                ShownFile(path="/plots/oom.png", mime="image/png", size=1234, caption="五次 OOM"),
                ShownFile(path="/notes.md", mime="text/markdown", size=88, caption=""),
            ],
            card=True,
            ms=steps[0].ms,
        )
    ]


def test_show_file_is_its_files_and_nothing_else():
    """`show_file` has no result worth a card: the file IS its rendering. One
    that declared nothing (an unresolvable path) keeps the card, so the
    failure stays visible — same as the FE."""
    shown = declare_shown_files("", [{"path": "/a.png", "mime": "image/png", "size": 9}])
    steps = build_timeline(
        title="t",
        messages=[
            {"role": "tool", "tool_name": "show_file", "content": shown},
            {"role": "tool", "tool_name": "show_file", "content": "no such file: /b.png"},
        ],
        options=VideoOptions(),
    ).steps

    assert [(s.card, len(s.files)) for s in steps if isinstance(s, ToolStep)] == [
        (False, 1),
        (True, 0),
    ]


def test_a_layout_declaration_plays_as_each_of_its_files():
    """`show_file(layout=…)` (#847) declares the tree AND every leaf in the flat
    list. The chat draws one card that opens the arrangement; the video has no
    panes to open, so it shows each file — the flat list is what it reads."""
    tree = {
        "type": "split",
        "dir": "row",
        "ratio": 0.5,
        "a": {"type": "leaf", "path": "/a.png"},
        "b": {"type": "leaf", "path": "/b.csv"},
    }
    shown = declare_shown_files(
        "A layout of 2 files",
        [
            {"path": "/a.png", "mime": "image/png", "size": 9},
            {"path": "/b.csv", "mime": "text/csv", "size": 9},
        ],
        layout=tree,
        caption="linked",
    )
    [step] = build_timeline(
        title="t",
        messages=[{"role": "tool", "tool_name": "show_file", "content": shown}],
        options=VideoOptions(),
    ).steps

    assert isinstance(step, ToolStep)
    assert (step.card, [f.path for f in step.files]) == (False, ["/a.png", "/b.csv"])


def test_the_timeline_names_every_workspace_path_it_will_want_bytes_for():
    """Declared IMAGES and `![](path)` images in answers — but not a declared
    non-image (a CSV is a card by the chat's own rule, `isInlineImage`, and a
    card needs no bytes: reading them was a wasted read and, in round 4, a
    false "will not be drawn"), not an image at a URL (the page fetches
    nothing) and not a link. This is the list a job prefetches before
    handing the render to a thread."""
    tl = build_timeline(
        title="t",
        messages=[
            {
                "role": "tool",
                "tool_name": "show_file",
                "content": declare_shown_files(
                    "",
                    [
                        {"path": "/plots/a.png", "mime": "image/png", "size": 1},
                        {"path": "/data/t.csv", "mime": "text/csv", "size": 9},
                    ],
                ),
            },
            {
                "role": "assistant",
                "author": "AI",
                "content": "see ![chart](plots/b.png) and ![ext](https://x/y.png) and [doc](/c.md)",
            },
        ],
        options=VideoOptions(),
    )

    assert tl.referenced_paths() == ["/plots/a.png", "/plots/b.png"]
    assert tl.wanted_files() == [("/plots/a.png", "image/png"), ("/plots/b.png", "")]


def test_the_prefetch_list_never_climbs_above_the_root():
    """`referenced_paths()` is what a job reads through the item's file
    facade, which does not jail `..` — so a path that climbs above the root
    is not on it. It stays in `wanted_files()`, so the page's verdict (and
    the CLI's note) still names it as not handed over."""
    tl = build_timeline(
        title="t",
        messages=[
            {
                "role": "assistant",
                "author": "AI",
                "content": "![a](../secret.png) ![b](plots/a.png)",
            },
            {
                "role": "tool",
                "tool_name": "show_file",
                "content": declare_shown_files(
                    "", [{"path": "/../s.png", "mime": "image/png", "size": 1}]
                ),
            },
        ],
        options=VideoOptions(),
    )

    assert tl.wanted_files() == [
        ("/../secret.png", ""),
        ("/plots/a.png", ""),
        ("/../s.png", "image/png"),
    ]
    assert tl.referenced_paths() == ["/plots/a.png"]


def test_a_size_too_big_for_a_float_is_kept_not_a_traceback():
    """A `size` written as a 400-digit integer: the browser's `JSON.parse`
    gives `Infinity` (a number, so the FE keeps the entry); Python's json
    gives an int that `math.isfinite` cannot even convert — `OverflowError`.
    Kept. (The float spelling `1e400` is `inf` in Python and is dropped —
    a recorded difference, like the bare `NaN` / `Infinity` literals.)"""
    steps = build_timeline(
        title="t",
        messages=[
            {
                "role": "tool",
                "tool_name": "show_file",
                "content": "body\n[shown-files]"
                + json.dumps(
                    {"shown_files": [{"path": "/a.png", "mime": "image/png", "size": 10**400}]}
                ),
            }
        ],
        options=VideoOptions(),
    ).steps

    assert isinstance(steps[0], ToolStep) and steps[0].files[0].size == 10**400


def test_a_declaration_is_parsed_as_the_fe_parses_it():
    """`shownFiles.ts` `JSON.parse`s everything after the marker; junk before
    the brace fails the whole declaration there, so it must here. A size that
    is not a finite number (`NaN`/`Infinity`, which Python's json accepts and
    the browser's does not) skips the entry rather than crashing. One
    declaration nested absurdly deep is a `RecursionError` in Python's parser
    and a `SyntaxError` in V8: both mean "no declaration", not a traceback."""
    deep = "body\n[shown-files]" + "[" * 100_000
    steps = build_timeline(
        title="t",
        messages=[{"role": "tool", "tool_name": "x", "content": deep}],
        options=VideoOptions(),
    ).steps
    assert isinstance(steps[0], ToolStep) and steps[0].files == []
    junk = (
        "body\n[shown-files]junk"
        + '{"shown_files":[{"path":"/a.png","mime":"image/png","size":1}]}'
    )
    nan = (
        'body\n[shown-files]{"shown_files":[{"path":"/a.png","mime":"image/png","size":NaN},'
        '{"path":"/b.png","mime":"image/png","size":2.9}]}'
    )
    steps = build_timeline(
        title="t",
        messages=[
            {"role": "tool", "tool_name": "x", "content": junk},
            {"role": "tool", "tool_name": "x", "content": nan},
        ],
        options=VideoOptions(),
    ).steps

    assert isinstance(steps[0], ToolStep) and steps[0].files == []
    assert isinstance(steps[1], ToolStep) and [(f.path, f.size) for f in steps[1].files] == [
        ("/b.png", 2)
    ]


def test_a_declaration_that_is_not_valid_json_or_not_the_shape_declares_nothing():
    """A hand-edited marker line that does not parse, or parses to the wrong
    shape, is treated as no declaration — the body is still shown, nothing
    crashes, no card is invented."""
    for tail in (
        "[shown-files]{not json",
        "[shown-files][1,2]",
        '[shown-files]{"shown_files": "x"}',
    ):
        steps = build_timeline(
            title="t",
            messages=[{"role": "tool", "tool_name": "x", "content": "body\n" + tail}],
            options=VideoOptions(),
        ).steps
        assert isinstance(steps[0], ToolStep) and steps[0].files == []
    # entries that are not objects are skipped, the good one kept
    steps = build_timeline(
        title="t",
        messages=[
            {
                "role": "tool",
                "tool_name": "x",
                "content": declare_shown_files(
                    "",
                    ["nope", {"path": "/a.png", "mime": "image/png", "size": 1}],  # ty: ignore[invalid-argument-type]
                ),
            }
        ],
        options=VideoOptions(),
    ).steps
    assert isinstance(steps[0], ToolStep) and [f.path for f in steps[0].files] == ["/a.png"]


def test_a_path_shown_twice_is_wanted_once():
    """The same chart declared by the plot tool, then by `show_file`, then
    embedded in the answer: one read, not three."""
    shown = declare_shown_files("", [{"path": "/p.png", "mime": "image/png", "size": 1}])
    tl = build_timeline(
        title="t",
        messages=[
            {"role": "tool", "tool_name": "sci_plot", "content": shown},
            {"role": "tool", "tool_name": "show_file", "content": shown},
            {"role": "assistant", "author": "AI", "content": "![](p.png)"},
        ],
        options=VideoOptions(),
    )

    assert tl.referenced_paths() == ["/p.png"]
