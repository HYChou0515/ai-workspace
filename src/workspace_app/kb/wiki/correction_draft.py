"""AI-assisted correction drafting (#397 Q12) — turn a KB chat answer the user
flagged as wrong into a ready-to-submit correction directive, with a bounded
mini-grill when the fault isn't clear from context.

The "回報有誤" dialog opens blank; one click runs this. It's **adaptive**: if the
model can tell what's wrong from the Q&A + the cited wiki pages, it drafts the
correction in one shot (0 questions); if not, it asks 1–3 short clarifying
questions (a HARD cap — more is noisy). The user answers, and the next call folds
those answers in and drafts. The user always reviews/edits the draft before it's
submitted, so a best-effort draft is fine — this only lowers typing friction.

LLM-streaming-only (``ILlm.collect`` streams under the hood); the model returns a
small JSON decision we parse here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import msgspec

if TYPE_CHECKING:
    from ..llm import ILlm, OnChunk

MAX_QUESTIONS = 3  # Q12: at most 3 clarifying questions, ever — more is noisy


class QA(msgspec.Struct):
    """One answered clarifying question from a prior drafting round."""

    question: str
    answer: str


class CorrectionDraft(msgspec.Struct):
    """The drafting step's result: either a ready ``draft`` (the user edits +
    submits) or an ``ask`` for a few clarifying questions."""

    action: Literal["draft", "ask"]
    instruction: str = ""
    target_page: str = ""
    questions: list[str] = msgspec.field(default_factory=list)


_PROMPT = """\
A user is reading a knowledge-base wiki and thinks an answer it gave is WRONG.
Your job is to help them file a correction for the wiki maintainer.

The user's question:
{question}

The wiki's answer (which the user says is wrong):
{answer}
{pages}{prior}
Decide ONE of:
- If you can tell what is wrong and how the wiki should read, write the
  correction. Respond with JSON:
  {{"action": "draft", "instruction": "<the correct fact>", "target_page": "<page or empty>"}}
- If you cannot tell what specifically is wrong, ask up to {remaining} SHORT
  clarifying question(s). Respond with JSON:
  {{"action": "ask", "questions": ["<question>", ...]}}

Keep `instruction` concise and factual — state the correct fact, not a rewrite of
the whole page. Respond with ONLY the JSON object.\
"""


def _pages_block(wiki_pages: list[str]) -> str:
    if not wiki_pages:
        return ""
    joined = "\n".join(f"- {p}" for p in wiki_pages)
    return f"\nThe wiki pages this answer cited (likely where the error is):\n{joined}\n"


def _prior_block(answered: list[QA]) -> str:
    if not answered:
        return ""
    joined = "\n".join(f"Q: {qa.question}\nA: {qa.answer}" for qa in answered)
    return f"\nThe user has already answered these clarifying questions:\n{joined}\n"


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of the model's output (tolerating prose or
    code fences around it). ``None`` if there's nothing parseable."""
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j <= i:
        return None
    try:
        obj = msgspec.json.decode(text[i : j + 1].encode())
    except msgspec.DecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def draft_correction(
    llm: ILlm,
    *,
    question: str,
    answer: str,
    wiki_pages: list[str] | None = None,
    answered: list[QA] | None = None,
    max_questions: int = MAX_QUESTIONS,
    on_chunk: OnChunk | None = None,
) -> CorrectionDraft:
    """Draft a wiki correction from a flagged Q&A, or ask a few clarifying
    questions (adaptive, capped — Q12). ``answered`` carries prior rounds' Q&A;
    once ``max_questions`` have been asked, a draft is forced (no more questions).
    Robust to a garbage model reply: falls back to a best-effort draft the user
    can edit."""
    answered = answered or []
    remaining = max(0, max_questions - len(answered))
    prompt = _PROMPT.format(
        question=question,
        answer=answer,
        pages=_pages_block(wiki_pages or []),
        prior=_prior_block(answered),
        remaining=remaining,
    )
    raw = llm.collect(prompt, on_chunk)
    obj = _extract_json(raw)
    if obj is None:
        # Model didn't return JSON — treat its text as a best-effort draft.
        return CorrectionDraft(action="draft", instruction=raw.strip())

    action = obj.get("action")
    questions = [str(q) for q in obj.get("questions", []) if str(q).strip()]
    # Force a draft when the question budget is spent or the model asked without
    # actually providing questions.
    if action == "ask" and remaining > 0 and questions:
        return CorrectionDraft(action="ask", questions=questions[:remaining])
    return CorrectionDraft(
        action="draft",
        instruction=str(obj.get("instruction", "")).strip(),
        target_page=str(obj.get("target_page", "")).strip(),
    )
