"""Answer formatters (#377) — shaping a human's term answer into a card
(title, body). The LLM formatter is pinned against a fake ``ILlm``; a bad
response falls back to the human's words verbatim (never invents)."""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.answer_formatter import LlmAnswerCardFormatter, VerbatimAnswerFormatter
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, response: str) -> None:
        self._response = response
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield (self._response, False)


def test_verbatim_formatter_uses_term_and_answer_unchanged():
    assert VerbatimAnswerFormatter().format(term="M4", answer="fourth metal layer") == (
        "M4",
        "fourth metal layer",
    )


def test_llm_formatter_parses_title_and_body():
    raw = json.dumps({"title": "Metal 4", "body": "The fourth metal layer."})
    title, body = LlmAnswerCardFormatter(_FakeLlm(raw)).format(term="M4", answer="the 4th metal")
    assert title == "Metal 4"
    assert body == "The fourth metal layer."


def test_llm_formatter_prompt_carries_the_term_and_answer():
    llm = _FakeLlm(json.dumps({"title": "t", "body": "b"}))
    LlmAnswerCardFormatter(llm).format(term="R7", answer="a reflow recipe")
    (prompt,) = llm.prompts
    assert "R7" in prompt
    assert "a reflow recipe" in prompt


def test_llm_formatter_falls_back_to_verbatim_on_unparseable_json():
    title, body = LlmAnswerCardFormatter(_FakeLlm("sorry, no JSON")).format(
        term="M4", answer="the answer"
    )
    assert (title, body) == ("M4", "the answer")


def test_llm_formatter_falls_back_when_fields_are_missing_or_wrong_typed():
    raw = json.dumps({"title": 5})  # title wrong-typed, body missing
    title, body = LlmAnswerCardFormatter(_FakeLlm(raw)).format(term="M4", answer="the answer")
    assert (title, body) == ("M4", "the answer")


def test_llm_formatter_peels_an_object_with_a_nested_value():
    # A nested object exercises brace-depth tracking (inner } doesn't close the top).
    raw = json.dumps({"meta": {"k": 1}, "title": "Metal 4", "body": "The 4th metal."})
    title, body = LlmAnswerCardFormatter(_FakeLlm(raw)).format(term="M4", answer="raw")
    assert (title, body) == ("Metal 4", "The 4th metal.")


def test_llm_formatter_falls_back_on_an_unterminated_object():
    # An opening brace with no matching close → _first_json_object raises → verbatim.
    title, body = LlmAnswerCardFormatter(_FakeLlm('{"title": "x"')).format(term="M4", answer="raw")
    assert (title, body) == ("M4", "raw")
