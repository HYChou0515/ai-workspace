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


class TypeStep(msgspec.Struct, tag="type"):
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

# A bubble that simply appears (error, note) is on screen this long before
# the next step, so it can be read.
_APPEAR_MS = 1400


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


class Timeline(msgspec.Struct):
    title: str
    steps: list[Step]
    estimated_ms: int
    """How long the steps take as written (``speed`` already applied) — the
    number the ceiling is checked against."""
    time_scale: float
    """What the player multiplies every delay by. ``1.0`` when the estimate
    fits ``max_seconds``; below that, the uniform squeeze that makes it fit.
    Uniform on purpose: dropping messages or truncating the tail would make
    a video that lies about the conversation."""


def build_timeline(
    *, title: str, messages: list[dict[str, Any]], options: VideoOptions
) -> Timeline:
    steps = [_scaled(s, options.speed) for s in _steps(messages, options)]
    camera = sum(2 * options.zoom_ms for s in steps if isinstance(s, TypeStep))
    estimated = sum(s.ms for s in steps) + round(camera / options.speed)
    ceiling = options.max_seconds * 1000
    scale = 1.0 if estimated <= ceiling else ceiling / estimated
    return Timeline(title=title, steps=steps, estimated_ms=estimated, time_scale=scale)


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
                        ms=len(reasoning) * options.stream_ms,
                    )
                )
            steps.append(
                StreamStep(
                    author=author, text=text, reasoning=False, ms=len(text) * options.stream_ms
                )
            )
        elif role == "tool":
            args = m.get("tool_args")
            steps.append(
                ToolStep(
                    name=str(m.get("tool_name") or "tool"),
                    args=dict(args) if isinstance(args, dict) else {},
                    output=_cut(text, options.tool_output_chars),
                    ms=options.tool_pause_ms,
                )
            )
        elif role == "user":
            steps.append(
                TypeStep(
                    author=str(m.get("author") or "user"),
                    text=text,
                    ms=len(text) * options.type_ms,
                )
            )
        elif role == "error":
            steps.append(ErrorStep(text=text, kind=str(m.get("error_kind") or ""), ms=_APPEAR_MS))
        else:
            steps.append(NoteStep(text=text, ms=_APPEAR_MS))
    return steps
