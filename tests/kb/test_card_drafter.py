"""LlmCardDrafter — one document → what it STATES about the terms it uses.

The drafter used to write a definition per document and rate its own confidence,
and to ask a question when it could not. All three are gone, and each for the
same reason: no single document can define a term the whole corpus talks about,
so a body written here is one facet that the merge could only ever choose
between. What a document can supply is a claim plus the sentence that makes it.

The parse and the QUOTE GATE live in ``kb.cards.extract`` and are shared with the
offline tuning loop — one criterion, one implementation, so what a person tuned
is what ships. These tests cover the drafter's own contract: the prompt it sends,
the shape it returns, and what it does with a reply it cannot use.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from workspace_app.kb.card_drafter import LlmCardDrafter, NullCardDrafter, drafting_prompt
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._reply, False


DOC = "回焊爐是一種加熱設備。錫膏在此熔融。"


def _reply(*cards: str) -> str:
    return '{"cards": [' + ", ".join(cards) + "]}"


def _card(term: str, claim: str, quote: str) -> str:
    return (
        f'{{"term": "{term}", "keys": ["{term}"], '
        f'"statements": [{{"text": "{claim}", "quote": "{quote}"}}]}}'
    )


def test_null_drafter_proposes_nothing():
    """The drafter used when no LLM is configured: the feature stays mounted and a
    run completes with zero proposals instead of 503-ing."""
    got = NullCardDrafter().digest(doc_path="a.md", doc_text=DOC)
    assert got.cards == []


def test_a_document_yields_the_terms_it_states_something_about():
    llm = _FakeLlm(_reply(_card("回焊爐", "是一種加熱設備", "回焊爐是一種加熱設備")))

    (card,) = LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC).cards

    assert card.term == "回焊爐"
    assert [(s.text, s.quote) for s in card.statements] == [
        ("是一種加熱設備", "回焊爐是一種加熱設備")
    ]


def test_a_claim_whose_quote_is_not_in_the_document_is_dropped():
    """The gate, and the whole defence against a card the corpus never supported.
    It costs no model call: a claim the model could not quote is a claim the
    document did not make."""
    llm = _FakeLlm(
        _reply(
            _card("回焊爐", "是一種加熱設備", "回焊爐是一種加熱設備"),
            _card("回焊爐", "屬於薔薇科", "回焊爐屬於薔薇科"),
        )
    )

    (card,) = LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC).cards

    assert [s.text for s in card.statements] == ["是一種加熱設備"]


def test_the_drafter_never_asks_questions():
    """It used to raise a question for a term it could not define. Removing that
    without adding silence would have left only guessing, so the third branch is
    to produce nothing — and the fields stay, always empty, because removing
    resource-facing shapes needs a migration."""
    llm = _FakeLlm(_reply(_card("回焊爐", "是一種加熱設備", "回焊爐是一種加熱設備")))

    got = LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC)

    assert got.term_questions == []
    assert got.description_questions == []


def test_a_term_the_document_only_mentions_yields_nothing():
    """No statement, no card. A headword whose card answers nothing is worse than
    no headword: a reader looks it up and learns that it exists."""
    llm = _FakeLlm('{"cards": [{"term": "SPI", "keys": ["SPI"], "statements": []}]}')

    assert LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC).cards == []


def test_the_prompt_is_the_one_the_tuning_loop_scored():
    """`--tune-round` improves a prompt file; if the drafter read a different one,
    every round of that tuning would be measuring something that never ships."""
    from workspace_app.kb.cards.extract import built_in_prompt

    llm = _FakeLlm('{"cards": []}')
    LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC)

    assert llm.prompts[0] == built_in_prompt().replace("{text}", DOC)


def test_the_prompt_carries_the_document_however_the_template_marks_the_slot():
    """`{text}` is what the tuned prompt uses; `{document}` and `{path}` are the
    older markers and are honoured so a hand-written template keeps working."""
    assert drafting_prompt("body", doc_path="p.md", template="A {document} at {path}") == (
        "A body at p.md"
    )
    assert drafting_prompt("body", template="B {text}") == "B body"


def test_drafting_prompt_leaves_the_json_example_intact():
    """The prompt describes a JSON reply, so it is full of braces. Substituting
    with `str.format` would read those as fields and raise — on a file a person is
    told to dump and edit."""
    template = 'Answer {"cards": [{"term": "..."}]} for {text}'
    assert drafting_prompt("D", template=template) == 'Answer {"cards": [{"term": "..."}]} for D'


def test_a_reply_with_no_usable_json_yields_nothing_and_warns(caplog):
    """One document out of hundreds answers with prose. Raising would end the run;
    warning leaves it diagnosable instead of a silently green run with no cards."""
    llm = _FakeLlm("I could not find any terms in this document.")

    with caplog.at_level(logging.WARNING, logger="workspace_app.kb.card_drafter"):
        got = LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC)

    assert got.cards == []
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "a.md" in logged and "no usable cards" in logged


def test_a_reply_whose_every_claim_fails_the_gate_says_so(caplog):
    """A different failure from "the model said nothing": the model DID answer and
    every quote was invented. Logged apart, because the fix is a different one."""
    llm = _FakeLlm(_reply(_card("回焊爐", "屬於薔薇科", "回焊爐屬於薔薇科")))

    with caplog.at_level(logging.WARNING, logger="workspace_app.kb.card_drafter"):
        got = LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC)

    assert got.cards == []
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "quote gate" in logged and "offered=1" in logged


def test_a_fenced_or_prefixed_reply_is_tolerated():
    """Small models wrap JSON in fences and add preambles."""
    body = _reply(_card("回焊爐", "是一種加熱設備", "回焊爐是一種加熱設備"))
    llm = _FakeLlm(f"Sure! Here you go:\n```json\n{body}\n```\nHope that helps.")

    (card,) = LlmCardDrafter(llm).digest(doc_path="a.md", doc_text=DOC).cards

    assert card.term == "回焊爐"


def test_the_number_of_cards_is_capped():
    """A pathological reply must not flood review."""
    many = _reply(*[_card(f"T{i}", "是一種加熱設備", "回焊爐是一種加熱設備") for i in range(9)])
    llm = _FakeLlm(many)

    got = LlmCardDrafter(llm, max_cards=3).digest(doc_path="a.md", doc_text=DOC)

    assert len(got.cards) == 3
