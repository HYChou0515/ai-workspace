"""Model sanity battery (Diagnostics) — run a fixed set of behavioural prompts
against each configured chat model, once per reasoning level, and surface the
outputs in a matrix the operator eyeballs (with a mechanical green/red dot where
a question can be graded automatically).

The question set is a CODE registry (`questions.QUESTIONS`) because each
question's grader / aux hint is real logic, not data a CSV could hold. Results
are a current-only specstar resource (`SanityResult`); a run is a `SanityRun`
job. See `docs`/the design discussion for the locked decisions.
"""

from __future__ import annotations

from .questions import (
    ALL_LEVELS,
    LEVEL_LABELS,
    QUESTIONS,
    Effort,
    SanityQuestion,
    auto_run_cells,
    find_question,
    messages_to_prompt,
    question_key,
    user,
)

__all__ = [
    "ALL_LEVELS",
    "LEVEL_LABELS",
    "QUESTIONS",
    "Effort",
    "SanityQuestion",
    "auto_run_cells",
    "find_question",
    "messages_to_prompt",
    "question_key",
    "user",
]
