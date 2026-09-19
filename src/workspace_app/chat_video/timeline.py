"""From a chat export to the steps the player performs.

Input is exactly what ``kb.chat_export.build_chat_export`` writes — a title and
the conversation's messages as ``to_builtins`` renders a ``Message`` — so a
hand-edited download and a live chat handed over by a route take the same
path. Every step carries its own time estimate in milliseconds, which is what
lets the whole thing be bounded (``VideoOptions.max_seconds``) before a browser
is ever opened.
"""

from __future__ import annotations

import json
import math
from typing import Any

import msgspec

from ..agent.shown_files import SHOWN_FILES_KEY, SHOWN_FILES_MARKER, split_declaration
from . import markdown as md
from .options import VideoOptions


class TypeStep(msgspec.Struct, tag="typing"):
    """A person's turn: focus the composer, type the text, send it."""

    author: str
    text: str
    ms: int


class StreamStep(msgspec.Struct, tag="stream"):
    """The assistant's turn, streamed character by character. ``reasoning``
    marks the thinking block, shown dimmed and before the answer. ``stopped``
    is the message's ``stopped_reason`` (#113: cut off mid-stream), shown as
    a small label — a video that showed a clean stop would misreport the
    run."""

    author: str
    text: str
    reasoning: bool
    ms: int
    stopped: str = ""


class ShownFile(msgspec.Struct):
    """A workspace file a tool put in front of the user (``[shown-files]``,
    ``agent/shown_files.py``): the chat shows an image inline and any other
    file as a card. The bytes are not here — the transcript never carries
    them — the renderer is handed them under ``path``."""

    path: str
    mime: str
    size: int
    caption: str = ""


class ToolStep(msgspec.Struct, tag="tool"):
    """A tool call as a card: the call is shown, the card spins for ``ms``,
    then the output opens — cut to ``VideoOptions.tool_output_chars`` — and
    the files it declared appear under it. ``card`` is false for a
    ``show_file`` that declared something: the file IS its rendering (the
    FE's rule), so no card at all."""

    name: str
    args: dict[str, Any]
    output: str
    files: list[ShownFile]
    card: bool
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
# chosen: the sample as first shipped (485 characters the player inserts;
# today's sample plays 593) ran 3 s over an estimate that ignored it, at
# `speed=1.5`. It is an approximation: the
# measured cost is ~6.5 ms/char at speed 1 and falls as the asked delays
# shrink (~4 ms at speed 2, ~2 ms under a hard squeeze — timers coalesce),
# so a squeezed recording lands a few percent UNDER its `playback_ms`, never
# over. Kept as one unscaled constant on purpose: over-estimating is the safe
# direction for the recorder's deadline. The player does not read this.
CHAR_OVERHEAD_MS = 5

_PUNCT = set(",.;:!?，。；：！？")


def cut_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def shown_files_in(result: str) -> tuple[str, list[ShownFile]]:
    """``(body, files)`` — the tool result without its declaration line, and
    the files it declared. The FE's parse (`renderers/shownFiles.ts`):
    ``JSON.parse`` of everything after the marker — junk before the brace
    fails the whole declaration — then the ``shown_files`` list; an entry
    needs a non-empty ``path`` and ``mime`` and a finite numeric ``size``
    (rounded down here), ``caption`` is optional, a malformed entry is
    skipped and the rest kept."""
    body, declaration = split_declaration(result)
    if not declaration:
        return result, []
    try:
        parsed = json.loads(declaration[len(SHOWN_FILES_MARKER) :])
    except (ValueError, RecursionError):  # not JSON; or nested past the parser
        return body, []
    raw = parsed.get(SHOWN_FILES_KEY) if isinstance(parsed, dict) else None
    files: list[ShownFile] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        path, mime, size = entry.get("path"), entry.get("mime"), entry.get("size")
        if not (isinstance(path, str) and path and isinstance(mime, str) and mime):
            continue
        # `size: 1e400` written as an integer is `Infinity` to the browser (a
        # number, kept); to Python an int `math.isfinite` cannot convert.
        if isinstance(size, bool) or not (
            isinstance(size, int) or (isinstance(size, float) and math.isfinite(size))
        ):
            continue
        caption = entry.get("caption")
        files.append(
            ShownFile(
                path=md.abs_path(path),
                mime=mime,
                size=int(size),
                caption=caption if isinstance(caption, str) else "",
            )
        )
    return body, files


def _typed_ms(text: str, per_char: int) -> int:
    """What the player's `typeInto` ASKS for on `text`: one delay per
    character, `punct_factor` more on each punctuation mark. The browser's
    own cost per character is counted separately (`Timeline.overhead_ms`)."""
    return sum(per_char + (per_char * PACING["punct_factor"] if ch in _PUNCT else 0) for ch in text)


class Timeline(msgspec.Struct):
    title: str
    steps: list[Step]

    def wanted_files(self) -> list[tuple[str, str]]:
        """Every reference the page would draw as a PICTURE given the bytes,
        in reading order: a tool's declaration whose mime is ``image/*``
        (the chat's own rule, ``isInlineImage`` — a declared CSV is a card
        by that rule, drawn without bytes, so it is not wanted and its bytes
        are never read), and an answer's ``![](path)`` (mime ``""``: the
        bytes are sniffed), through the same markdown parse the page draws
        with (``markdown.image_paths``). Not a URL (the page fetches
        nothing), not a link. The ONE walk: ``referenced_paths`` and the
        page's asset table are both views of it."""
        out: list[tuple[str, str]] = []
        for step in self.steps:
            if isinstance(step, ToolStep):
                out.extend((f.path, f.mime) for f in step.files if f.mime.startswith("image/"))
            elif isinstance(step, StreamStep) and not step.reasoning:
                out.extend((p, "") for p in md.image_paths(step.text))
        return out

    def referenced_paths(self) -> list[str]:
        """The paths of ``wanted_files``, once each, minus any that climbs
        above the root — the list a job prefetches through the item's file
        facade (which does not jail ``..``) and the CLI reads from
        ``--files``. The climbing one stays in ``wanted_files``, so the page's
        verdict — and the CLI's note — still says it was not handed over."""
        seen: list[str] = []
        for path, _mime in self.wanted_files():
            if path not in seen and not md.escapes_root(path):
                seen.append(path)
        return seen

    estimated_ms: int
    """What the transcript ASKS for: the delays (``speed`` applied) plus
    ``overhead_ms`` — the number the ceiling is checked against, BEFORE any
    squeeze. For how long the recording will run, read ``playback_ms``."""
    overhead_ms: int
    """The browser's own per-character cost, which no option shrinks."""
    time_scale: float
    """What the player multiplies every asked delay by. ``1.0`` when the
    estimate fits ``max_seconds``; below that, the uniform squeeze that makes
    it fit — computed on the asked delays alone, since the overhead does not
    squeeze. Uniform on purpose: dropping messages or truncating the tail
    would make a video that lies about the conversation."""

    @property
    def playback_ms(self) -> int:
        """How long the recording will run: the asked delays after the
        squeeze, plus the overhead. The number a person is told and the one
        the recorder's deadline is set from. Equals ``estimated_ms`` when
        nothing was squeezed and ``max_seconds`` (in ms, ±1 of rounding)
        when it landed on the ceiling; it is over the ceiling when the
        overhead alone is, or when the squeeze hit its 5% floor."""
        asked = self.estimated_ms - self.overhead_ms
        return round(asked * self.time_scale) + self.overhead_ms


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
        # simply run long — `playback_ms` says by how much, and the recorder's
        # deadline is set from that number, not from the ceiling. The floor
        # itself is the other way past the ceiling: 5% of a huge ask is still
        # more than `max_seconds`.
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
                        stopped=str(m.get("stopped_reason") or ""),
                    )
                )
        elif role == "tool":
            args = m.get("tool_args")
            name = str(m.get("tool_name") or "tool")
            body, files = shown_files_in(text)
            steps.append(
                ToolStep(
                    name=name,
                    args=dict(args) if isinstance(args, dict) else {},
                    output=cut_text(body, options.tool_output_chars),
                    files=files,
                    card=not (name == "show_file" and files),
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
