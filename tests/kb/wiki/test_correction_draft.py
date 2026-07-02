"""#397 Q12 — adaptive AI drafting of a wiki correction from a flagged Q&A.

A fake ILlm returns scripted JSON so the tests exercise the decision logic (draft
vs ask), the question cap, and robustness to a non-JSON reply — not a real model.
"""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.correction_draft import QA, draft_correction


class _FakeLlm(ILlm):
    """A scripted ILlm — inherits the concrete streaming `collect`."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield (self._reply, False)


def test_drafts_in_one_shot_when_the_fault_is_clear():
    llm = _FakeLlm(
        '{"action": "draft", "instruction": "Founded in 1998, not 1989.", '
        '"target_page": "/entities/foo.md"}'
    )
    out = draft_correction(
        llm,
        question="When was Foo founded?",
        answer="Foo was founded in 1989.",
        wiki_pages=["/entities/foo.md"],
    )
    assert out.action == "draft"
    assert out.instruction == "Founded in 1998, not 1989."
    assert out.target_page == "/entities/foo.md"


def test_asks_clarifying_questions_when_unsure():
    llm = _FakeLlm('{"action": "ask", "questions": ["Which date is wrong?", "What is correct?"]}')
    out = draft_correction(llm, question="q", answer="a")
    assert out.action == "ask"
    assert out.questions == ["Which date is wrong?", "What is correct?"]


def test_question_cap_forces_a_draft_after_the_budget_is_spent():
    # Already answered 3 questions → remaining budget 0 → even an "ask" reply is
    # coerced to a draft (Q12: never more than 3 questions).
    llm = _FakeLlm('{"action": "ask", "questions": ["one more?"]}')
    answered = [QA(question=f"q{i}", answer=f"a{i}") for i in range(3)]
    out = draft_correction(llm, question="q", answer="a", answered=answered)
    assert out.action == "draft"


def test_ask_is_truncated_to_the_remaining_budget():
    llm = _FakeLlm('{"action": "ask", "questions": ["q1", "q2", "q3", "q4"]}')
    answered = [QA(question="q0", answer="a0")]  # 1 asked → 2 remaining
    out = draft_correction(llm, question="q", answer="a", answered=answered)
    assert out.action == "ask"
    assert len(out.questions) == 2


def test_prior_answers_are_shown_to_the_model():
    llm = _FakeLlm('{"action": "draft", "instruction": "fixed"}')
    draft_correction(
        llm,
        question="q",
        answer="a",
        answered=[QA(question="which?", answer="the date")],
    )
    assert "the date" in llm.prompts[0]  # prior Q&A folded into the prompt


def test_non_json_reply_falls_back_to_a_best_effort_draft():
    llm = _FakeLlm("The date should be 1998.")  # model ignored the JSON instruction
    out = draft_correction(llm, question="q", answer="a")
    assert out.action == "draft"
    assert out.instruction == "The date should be 1998."


def test_ask_without_questions_becomes_a_draft():
    llm = _FakeLlm('{"action": "ask", "questions": []}')
    out = draft_correction(llm, question="q", answer="a")
    assert out.action == "draft"


def test_malformed_json_object_falls_back_to_a_best_effort_draft():
    # Braces present but not valid JSON → DecodeError → best-effort draft.
    llm = _FakeLlm("{not: valid json,}")
    out = draft_correction(llm, question="q", answer="a")
    assert out.action == "draft"
    assert out.instruction == "{not: valid json,}"
