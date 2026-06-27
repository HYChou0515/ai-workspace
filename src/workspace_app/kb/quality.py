"""Issue #105 — judge a document's quality *as a knowledge source*.

``QualityScorer`` reads a ``SourceDoc``'s chunks against the collection's
user-authored ``quality_rubric`` and produces one holistic doc-level
``QualityAssessment`` (a 0–100 ``score`` + a per-dimension ``breakdown`` + a
short ``rationale``). The score later drives a query-independent down-weight in
retrieval (see ``retriever`` — second-phase additive document prior) and is shown
on the document list.

Design (grilled):

- **Chunk-based windowed map-reduce.** A ``SourceDoc`` is already split into
  chunks at index time, so we read *each* chunk — packed greedily into
  model-sized *windows* — assess every window against the rubric (**map**), then
  synthesise the per-window notes into one holistic doc-level result
  (**reduce**). Reading the chunks dissolves the long-document truncation problem
  (a single window always fits), and the window packing keeps the call count
  bounded while still reading the whole document.
- **Two-layer prompt.** The user's ``rubric`` says *what* "good" means and names
  the breakdown dimensions; this module fixes the *output format* (overall score
  + per-dimension breakdown + rationale) so the result is parseable regardless of
  how the rubric is written. The breakdown keys therefore vary per collection — a
  free-form ``dict[str, float]``.
- **Streaming + failure-safe.** Every LLM call goes through ``ILlm.collect`` (which
  drains ``stream``) so the live judging can be surfaced (``feedback_always_stream_llm``).
  An empty rubric, a doc with no chunks, or an unparseable reduce response yields
  ``None`` (the doc stays *un-scored* = the neutral default, never penalised) — it
  never raises, mirroring the repo's tolerant structured-LLM contract
  (``card_drafter`` / ``insight_extractor``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from .llm import ILlm, OnChunk

logger = logging.getLogger(__name__)

# The system-fixed output format (layer 2). The user's rubric (layer 1) is
# interpolated above it. `str.replace` (not `.format`) so the JSON braces in the
# template are left alone.
_MAP_PROMPT = (
    "You assess one section of a document for its quality as a knowledge source, "
    "using this rubric:\n\n{rubric}\n\n"
    "Here is the section:\n\n{window}\n\n"
    "Briefly note this section's quality signals against the rubric — strengths, "
    "weaknesses, and any noise (boilerplate, near-empty or duplicated text). Do "
    "not give a numeric score yet; just the notes."
)

_REDUCE_PROMPT = (
    "You score a whole document's quality as a knowledge source, using this "
    "rubric:\n\n{rubric}\n\n"
    "Here are per-section assessments covering the whole document:\n\n{notes}\n\n"
    "Now give the FINAL holistic judgment of the WHOLE document. Reply with ONLY a "
    "JSON object, nothing else:\n"
    '{"score": <integer 0-100>, "breakdown": {<dimension>: <number 0-1>, ...}, '
    '"rationale": <one short paragraph>}\n'
    "Use the dimensions named in the rubric as the breakdown keys."
)


@dataclass(frozen=True)
class QualityAssessment:
    """The doc-level verdict. ``score`` is the holistic 0–100 grade that drives the
    search down-weight; ``breakdown`` is the per-dimension sub-scores (keys named
    by the rubric, values in [0, 1]); ``rationale`` is the short justification."""

    score: int
    breakdown: dict[str, float]
    rationale: str


class QualityScorer:
    def __init__(self, llm: ILlm, *, window_chars: int = 6000) -> None:
        self._llm = llm
        self._window_chars = window_chars

    def score(
        self,
        *,
        rubric: str,
        chunks: Sequence[str],
        on_chunk: OnChunk | None = None,
    ) -> QualityAssessment | None:
        """Judge the doc. ``None`` (un-scored) when there is no rubric, no chunks,
        or the model's final response can't be parsed — never raises."""
        if not rubric.strip() or not chunks:
            return None
        notes = [
            self._llm.collect(
                _MAP_PROMPT.replace("{rubric}", rubric).replace("{window}", window),
                on_chunk=on_chunk,
            )
            for window in _pack_windows(chunks, self._window_chars)
        ]
        joined = "\n\n".join(f"[section {i + 1}] {n}" for i, n in enumerate(notes))
        raw = self._llm.collect(
            _REDUCE_PROMPT.replace("{rubric}", rubric).replace("{notes}", joined),
            on_chunk=on_chunk,
        )
        return _parse_assessment(raw)


def _pack_windows(chunks: Sequence[str], window_chars: int) -> list[str]:
    """Greedily pack chunks into windows no larger than ``window_chars`` (a single
    over-budget chunk still gets its own window — we never drop content). Reads
    every chunk; the window count bounds the map call count."""
    windows: list[str] = []
    cur: list[str] = []
    size = 0
    for ch in chunks:
        if cur and size + len(ch) > window_chars:
            windows.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(ch)
        size += len(ch)
    if cur:  # pragma: no branch — `score()` guards against empty chunks, so the
        windows.append("\n\n".join(cur))  # last window is always non-empty here
    return windows


def _parse_assessment(raw: str) -> QualityAssessment | None:
    """Tolerantly parse the reduce response into a ``QualityAssessment``. Peels the
    first ``{...}`` (small models wrap JSON in fences / preambles); clamps ``score``
    to 0–100; keeps only numeric breakdown values; ``None`` for any unrecoverable
    response (no JSON, no numeric score) — never raises."""
    try:
        obj = json.loads(_extract_json_object(raw))
    except (json.JSONDecodeError, ValueError):
        logger.warning("QualityScorer: LLM response was not parseable JSON")
        return None
    # `_extract_json_object` only ever returns a brace-balanced `{...}`, so a
    # successful parse is always a dict — no non-dict guard needed.
    raw_score = obj.get("score")
    if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
        return None
    score = max(0, min(100, round(raw_score)))
    breakdown: dict[str, float] = {}
    raw_breakdown = obj.get("breakdown")
    if isinstance(raw_breakdown, dict):
        for k, v in raw_breakdown.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                breakdown[str(k)] = float(v)
    rationale = obj.get("rationale")
    return QualityAssessment(
        score=score,
        breakdown=breakdown,
        rationale=rationale if isinstance(rationale, str) else "",
    )


def _extract_json_object(raw: str) -> str:
    """Return the substring from the first ``{`` to its matching ``}``. Tolerates a
    ```json fence or a preamble. (Kept local — mirrors the helpers in
    ``card_drafter`` / ``insight_extractor`` so this lean module pulls no heavy
    deps.)"""
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    depth = 0
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    raise ValueError("unterminated JSON object")
