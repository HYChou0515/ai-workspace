from collections.abc import Iterator

from workspace_app.kb.eval.generate import generate_question, is_answerable, make_question
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    """Returns queued replies in order; records the prompts it saw."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._replies.pop(0), False


def test_generate_question_returns_the_models_question_and_shows_it_the_passage():
    llm = _FakeLlm("What temperature does reflow zone 3 run at?")
    q = generate_question(llm, "Reflow zone 3 runs at 245C.")
    assert q == "What temperature does reflow zone 3 run at?"
    assert "Reflow zone 3 runs at 245C." in llm.prompts[0]


def test_is_answerable_is_true_only_on_a_leading_yes():
    assert is_answerable(_FakeLlm("Yes, clearly."), "q", "p") is True
    assert is_answerable(_FakeLlm("No — too vague."), "q", "p") is False


def test_make_question_keeps_an_answerable_question():
    llm = _FakeLlm("What temperature does zone 3 run at?", "yes")
    assert make_question(llm, "Zone 3 runs at 245C.") == "What temperature does zone 3 run at?"


def test_make_question_drops_a_question_the_model_rejects():
    llm = _FakeLlm("What is this about?", "no")
    assert make_question(llm, "Zone 3 runs at 245C.") is None
