"""From a chat export to the steps the player performs.

Input is exactly what ``kb.chat_export.build_chat_export`` writes — a title and
the conversation's messages as ``to_builtins`` renders a ``Message`` — so a
hand-edited download and a live chat handed over by a route take the same
path. Every step carries its own time estimate in milliseconds, which is what
lets the whole thing be bounded (``VideoOptions.max_seconds``) before a browser
is ever opened.
"""

from __future__ import annotations

from typing import Any

import msgspec

from .options import VideoOptions


class TypeStep(msgspec.Struct, tag="typing"):
    """A person's turn: focus the composer, type the text, send it."""

    author: str
    text: str
    ms: int


class StreamStep(msgspec.Struct, tag="stream"):
    """The assistant's turn, streamed character by character. ``reasoning``
    marks the thinking block, shown dimmed and before the answer."""

    author: str
    text: str
    reasoning: bool
    ms: int


class ToolStep(msgspec.Struct, tag="tool"):
    """A tool call as a card: the call is shown, the card spins for ``ms``,
    then the output opens — cut to ``VideoOptions.tool_output_chars``."""

    name: str
    args: dict[str, Any]
    output: str
    ms: int


class ErrorStep(msgspec.Struct, tag="error"):
    """The turn failed (``role="error"``): a red bubble, ``kind`` as its label."""

    text: str
    kind: str
    ms: int


class NoteStep(msgspec.Struct, tag="note"):
    """Anything else the thread holds — a `system` notice, a `mention`, a role
    this player does not know. A dim one-liner: it is nobody typing, so the
    camera stays where it is."""

    text: str
    ms: int


Step = TypeStep | StreamStep | ToolStep | ErrorStep | NoteStep

# The pauses the player takes that are not a character being typed. ONE table:
# the page embeds it and its script reads these names, and the estimate below
# adds the same numbers — a recording ran twice its estimate when the player
# kept its own copies of these.
PACING: dict[str, int] = {
    "lead_ms": 600,  # before the first step
    "tail_ms": 900,  # after the last, so the final frame can be read
    "zoom_settle_ms": 200,  # after a camera move, on top of zoom_ms
    "after_type_ms": 350,  # the typed message sits in the composer before send
    "after_answer_ms": 600,  # a finished answer stays before the next step
    "after_tool_ms": 700,  # an opened tool card stays before the next step
    "appear_ms": 1400,  # a bubble that simply appears (error, note) is read for this long
    "punct_factor": 2,  # a punctuation mark is held this many extra per-char delays
}

# What a headless browser spends per character on top of the asked delay —
# firing the timer, inserting the node, painting the frame. Measured, not
# chosen: a 600-character transcript ran 3 s over an estimate that ignored
# it. The player does not read this; the browser adds it by itself — which is
# also why neither `speed` nor the squeeze can shrink it.
CHAR_OVERHEAD_MS = 5

_PUNCT = set(",.;:!?，。；：！？")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _typed_ms(text: str, per_char: int) -> int:
    """What the player's `typeInto` ASKS for on `text`: one delay per
    character, `punct_factor` more on each punctuation mark. The browser's
    own cost per character is counted separately (`Timeline.overhead_ms`)."""
    return sum(per_char + (per_char * PACING["punct_factor"] if ch in _PUNCT else 0) for ch in text)


class Timeline(msgspec.Struct):
    title: str
    steps: list[Step]
    estimated_ms: int
    """How long the recording should run: the asked delays (``speed``
    applied) plus ``overhead_ms`` — the number the ceiling is checked
    against."""
    overhead_ms: int
    """The browser's own per-character cost, which no option shrinks."""
    time_scale: float
    """What the player multiplies every asked delay by. ``1.0`` when the
    estimate fits ``max_seconds``; below that, the uniform squeeze that makes
    it fit — computed on the asked delays alone, since the overhead does not
    squeeze. Uniform on purpose: dropping messages or truncating the tail
    would make a video that lies about the conversation."""


def build_timeline(
    *, title: str, messages: list[dict[str, Any]], options: VideoOptions
) -> Timeline:
    steps = [_scaled(s, options.speed) for s in _steps(messages, options)]
    edges = round((PACING["lead_ms"] + PACING["tail_ms"]) / options.speed)
    asked = sum(s.ms for s in steps) + edges
    overhead = CHAR_OVERHEAD_MS * sum(_chars_played(s) for s in steps)
    ceiling = options.max_seconds * 1000
    if asked + overhead <= ceiling or asked == 0:
        scale = 1.0
    else:
        # Squeeze only what can be squeezed. If the overhead alone is over the
        # ceiling the floor keeps the player moving at all; the recording will
        # simply run long, and the recorder's own timeout is the last resort.
        scale = max(0.05, (ceiling - overhead) / asked)
    return Timeline(
        title=title,
        steps=steps,
        estimated_ms=asked + overhead,
        overhead_ms=overhead,
        time_scale=scale,
    )


def _chars_played(step: Step) -> int:
    """Characters the player inserts one at a time for this step."""
    return len(step.text) if isinstance(step, TypeStep | StreamStep) else 0


def _scaled(step: Step, speed: float) -> Step:
    return msgspec.structs.replace(step, ms=round(step.ms / speed))


def _steps(messages: list[dict[str, Any]], options: VideoOptions) -> list[Step]:
    steps: list[Step] = []
    for m in messages:
        role = str(m.get("role") or "")
        text = str(m.get("content") or "")
        if role == "assistant":
            author = str(m.get("author") or "AI")
            reasoning = str(m.get("reasoning") or "")
            if reasoning:
                steps.append(
                    StreamStep(
                        author=author,
                        text=reasoning,
                        reasoning=True,
                        ms=_typed_ms(reasoning, options.stream_ms),
                    )
                )
            if text or not reasoning:  # a turn that only thought has no empty answer bubble
                steps.append(
                    StreamStep(
                        author=author,
                        text=text,
                        reasoning=False,
                        ms=_typed_ms(text, options.stream_ms) + PACING["after_answer_ms"],
                    )
                )
        elif role == "tool":
            args = m.get("tool_args")
            steps.append(
                ToolStep(
                    name=str(m.get("tool_name") or "tool"),
                    args=dict(args) if isinstance(args, dict) else {},
                    output=_cut(text, options.tool_output_chars),
                    ms=options.tool_pause_ms + PACING["after_tool_ms"],
                )
            )
        elif role == "user":
            camera = 2 * (options.zoom_ms + PACING["zoom_settle_ms"])  # in, and back out
            steps.append(
                TypeStep(
                    author=str(m.get("author") or "user"),
                    text=text,
                    ms=_typed_ms(text, options.type_ms) + PACING["after_type_ms"] + camera,
                )
            )
        elif role == "error":
            steps.append(
                ErrorStep(text=text, kind=str(m.get("error_kind") or ""), ms=PACING["appear_ms"])
            )
        else:
            steps.append(NoteStep(text=text, ms=PACING["appear_ms"]))
    return steps
