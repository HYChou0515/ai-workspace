"""SanityResult — one cell of the model-sanity matrix (one model × one question
× one reasoning level).

DERIVED + current-only: a re-run overwrites the same row (natural-key id), so the
matrix always shows the latest answer. The FE hydrates the grid by listing these
filtered to the selected ``model``; specstar's auto routes serve that for free.
"""

from __future__ import annotations

import hashlib

from msgspec import Struct


def sanity_result_id(model: str, question_key: str, level: str) -> str:
    """A deterministic, slash-free resource id for one matrix cell. ``model``
    holds a ``/`` (e.g. ``ollama_chat/qwen3:14b``) which specstar ids can't, so
    the natural key is hashed; the components stay on the row for querying."""
    raw = f"{model}\x00{question_key}\x00{level}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def sanity_verdict_id(model: str) -> str:
    """A deterministic, slash-free id for one model's fitness verdict (#231).
    One verdict per model (current-only, upsert), like a cell but keyed by model
    alone — ``model`` holds a ``/`` so the natural key is hashed."""
    return hashlib.sha256(model.encode("utf-8")).hexdigest()[:24]


class SanityResult(Struct):  # → resource "sanity-result"
    model: str  # litellm model string (indexed — the FE filters the matrix by it)
    question_key: str  # health.sanity.questions.question_key (hash of the messages)
    level: str  # reasoning level: none | low | medium | high
    output: str = ""  # the model's answer (non-reasoning content)
    reasoned: bool = False  # did the model emit thinking on this run?
    grade: str = ""  # "pass" | "fail" | "" (no mechanical grader → eyeball)
    ai_grade: str = ""  # #231: AI judge verdict "pass" | "fail" | "" (not judged yet)
    ai_note: str = ""  # #231: AI judge one-line rationale; "" when not judged
    aux: str = ""  # display-only hint (e.g. "312 字"); "" when none
    error: str = ""  # set when the run itself failed (the cell shows it red)
    latency_ms: int = 0


class SanityVerdict(Struct):  # → resource "sanity-verdict"
    """#231: one model's overall fitness verdict, written by the AI judge after
    reading all of that model's cells. Current-only (upsert by model)."""

    model: str  # litellm model string (indexed — one verdict per model)
    score: int = 0  # 0–100 overall fitness
    summary: str = ""  # markdown; per-role fitness bullets ("good for X, weak at Y")
