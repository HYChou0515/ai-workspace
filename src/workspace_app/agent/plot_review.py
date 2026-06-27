"""VLM visual self-correction loop for sci-plot charts (#285).

A small local VLM is good at *detecting* layout problems ("do the x labels
overlap?") but unreliable at *proposing* good matplotlib parameters. So this
splits the work: the VLM answers a fixed yes/no checklist (:func:`detect_issues`)
and a deterministic adjuster maps detected issues → presentation-knob tweaks
(:func:`adjust_style`), with the VLM's free-text notes as a soft hint. The loop
(:func:`run_review`) re-renders up to ``max_passes`` times, keeps the best
attempt, and never makes a chart worse.

The adjuster only ever touches *presentation* (figsize / dpi / font size / tick
rotation / padding) — never which column plays which role. A blank render is a
render failure, not a layout problem, so it is reported but not "fixed" by
restyling.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# What the VLM is asked to check. Keys map 1:1 onto ChartIssues booleans.
_CHECK_KEYS = ("blank", "overlap", "truncated", "tiny_text", "clipped", "missing_legend")
# The subset the deterministic adjuster can actually fix by restyling.
_BLOCKING = ("overlap", "truncated", "tiny_text", "clipped")

CHECKLIST_PROMPT = (
    "You are reviewing a generated chart image for layout problems only (not the "
    "data). Answer each item strictly with yes or no, one per line:\n"
    "blank: <yes if the image is empty/blank or has no plotted data>\n"
    "overlap: <yes if x-axis tick labels overlap or collide>\n"
    "truncated: <yes if any labels/text are cut off>\n"
    "tiny_text: <yes if text is too small to read>\n"
    "clipped: <yes if any plot elements run outside the figure frame>\n"
    "missing_legend: <yes if a legend or colorbar is needed but absent>\n"
    "notes: <one short line on the single most important layout issue, or 'none'>"
)


@dataclass(frozen=True)
class ChartIssues:
    blank: bool = False
    overlap: bool = False
    truncated: bool = False
    tiny_text: bool = False
    clipped: bool = False
    missing_legend: bool = False
    notes: str = ""

    @property
    def blocking_count(self) -> int:
        return sum(getattr(self, k) for k in _BLOCKING)

    @property
    def any_blocking(self) -> bool:
        return self.blocking_count > 0


def parse_issues(text: str) -> ChartIssues:
    """Lenient parse of the VLM's checklist answer. Each key defaults to False
    (conservative — don't trigger needless re-renders on unparseable output)."""
    low = text.lower()
    flags: dict[str, Any] = {}
    for key in _CHECK_KEYS:
        m = re.search(rf"{key}\s*[:\-]?\s*([a-z]+)", low)
        flags[key] = bool(m and m.group(1) in ("yes", "true", "y"))
    notes = ""
    nm = re.search(r"notes\s*[:\-]?\s*(.+)", text, flags=re.IGNORECASE)
    if nm:
        candidate = nm.group(1).strip()
        if candidate.lower() not in ("none", "n/a", ""):
            notes = candidate
    return ChartIssues(notes=notes, **flags)


def detect_issues(
    png: bytes,
    describer: Any,
    on_chunk: Callable[[str, bool], None] | None = None,
) -> ChartIssues:
    """Ask the VLM the fixed checklist about one rendered chart."""
    answer = describer.answer(png, "image/png", question=CHECKLIST_PROMPT, on_chunk=on_chunk)
    return parse_issues(answer)


def _wider(style: dict, *, taller: bool = False) -> list[float]:
    w, h = style.get("figsize") or (9.0, 5.5)
    return [round(w * 1.3, 1), round(h * (1.2 if taller else 1.0), 1)]


def adjust_style(style: dict, issues: ChartIssues) -> dict | None:
    """Map issues → presentation deltas (+ soft hints from notes). Returns a new
    merged style dict, or None when nothing is actionable."""
    delta: dict[str, Any] = {}
    if issues.overlap or issues.truncated:
        delta["x_tick_rotation"] = 35.0
        delta["figsize"] = _wider(style)
    if issues.tiny_text:
        delta["font_size"] = (style.get("font_size") or 10.0) + 2.0
        delta["dpi"] = max(style.get("dpi") or 110, 140)
    if issues.clipped:
        delta["pad"] = (style.get("pad") or 1.2) + 0.8
        delta["figsize"] = _wider(style, taller=True)
    # Soft hints: cues the booleans didn't already cover.
    hint = issues.notes.lower()
    if "rotat" in hint and "x_tick_rotation" not in delta:
        delta["x_tick_rotation"] = 45.0
    if ("cut off" in hint or "outside" in hint) and "figsize" not in delta:
        delta["figsize"] = _wider(style, taller=True)
    if not delta:
        return None
    merged = {**style, **delta}
    return merged if merged != style else None


@dataclass
class ReviewOutcome:
    image_path: str
    png: bytes
    passes: int
    fixed: list[str] = field(default_factory=list)
    remaining: ChartIssues | None = None
    history: list[ChartIssues] = field(default_factory=list)

    def summary(self) -> str:
        """One human/LLM-facing line on what the auto-review did."""
        if self.passes == 0:
            return "Visual check: no layout issues found."
        parts = []
        if self.fixed:
            parts.append("auto-fixed " + ", ".join(self.fixed))
        if self.remaining and self.remaining.any_blocking:
            still = [k for k in _BLOCKING if getattr(self.remaining, k)]
            parts.append("still " + ", ".join(still))
        body = "; ".join(parts) if parts else "no improvement"
        return f"Visual check ({self.passes} pass(es)): {body}."


Renderer = Callable[[dict], Awaitable[tuple[bytes, str]]]


def _fixed_between(old: ChartIssues, new: ChartIssues) -> list[str]:
    return [k for k in _BLOCKING if getattr(old, k) and not getattr(new, k)]


async def run_review(
    *,
    initial_png: bytes,
    initial_path: str,
    render: Renderer,
    describer: Any,
    on_chunk: Callable[[str, bool], None] | None = None,
    max_passes: int = 2,
) -> ReviewOutcome:
    """Detect → adjust → re-render, keeping the best attempt. ``render(style)``
    returns ``(png_bytes, path)`` for the given style dict. The first render is
    supplied (``initial_*``); only corrections call ``render``."""
    issues = detect_issues(initial_png, describer, on_chunk)
    history = [issues]
    best_png, best_path, best = initial_png, initial_path, issues
    style: dict = {}
    fixed: list[str] = []
    passes = 0
    while passes < max_passes and best.any_blocking:
        new_style = adjust_style(style, best)
        if new_style is None:
            break  # nothing actionable left
        style = new_style
        png, path = await render(style)
        new_issues = detect_issues(png, describer, on_chunk)
        passes += 1
        history.append(new_issues)
        if new_issues.blocking_count < best.blocking_count:
            fixed.extend(_fixed_between(best, new_issues))
            best_png, best_path, best = png, path, new_issues
        else:
            break  # no improvement → keep the best so far, stop (never worsen)
    remaining = best if best.any_blocking else None
    return ReviewOutcome(
        image_path=best_path,
        png=best_png,
        passes=passes,
        fixed=fixed,
        remaining=remaining,
        history=history,
    )
