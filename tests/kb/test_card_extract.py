"""Context cards, extracted as EVIDENCE rather than as written definitions.

A document does not know what the rest of the corpus says, so a definition
authored per document can only ever be one facet — and the pipeline that then
picks a winner between facets loses the rest. What a document CAN supply is what
it states, in its own words, with the sentence that states it. Those accumulate;
the definition is derived from them.

So the unit here is a `Statement`: a claim about a term, and the verbatim quote
that backs it. A claim whose quote is not in the document is not a claim the
document made, and it is dropped — the same gate the graph extractor already
applies to declared aliases, and it matters more here, because a card's entire
authority is that quote.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.cards.extract import extract_cards
from workspace_app.kb.llm import ILlm


class _Model(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._reply, False


def _reply(*cards: dict) -> str:
    return json.dumps({"cards": list(cards)}, ensure_ascii=False)


def test_a_term_the_document_defines_becomes_a_card_carrying_the_sentence():
    """The tracer bullet: what a document says about a term, plus the words that
    say it."""
    document = "本廠使用回焊爐進行焊接。回焊爐是一種加熱設備。"
    model = _Model(
        _reply(
            {
                "term": "回焊爐",
                "keys": ["回焊爐", "Reflow Oven"],
                "statements": [{"text": "是一種加熱設備", "quote": "回焊爐是一種加熱設備"}],
            }
        )
    )

    (card,) = extract_cards(model, document)

    assert card.term == "回焊爐"
    assert card.keys == ["回焊爐", "Reflow Oven"]
    assert [s.text for s in card.statements] == ["是一種加熱設備"]
    assert card.statements[0].quote == "回焊爐是一種加熱設備"


def test_a_claim_whose_quote_is_not_in_the_document_is_dropped():
    """The whole defence against invented definitions, and it costs no model
    call. A model free to invent the sentence it is quoting has given nobody
    anything to check — the requirement would be decoration. The graph extractor
    already gates declared aliases this way; a card needs it more, because the
    quote is the card's entire authority."""
    document = "本廠使用回焊爐進行焊接。回焊爐是一種加熱設備。"
    model = _Model(
        _reply(
            {
                "term": "回焊爐",
                "keys": ["回焊爐"],
                "statements": [
                    {"text": "是一種加熱設備", "quote": "回焊爐是一種加熱設備"},
                    {"text": "屬於 SMT 產線的迴焊段", "quote": "回焊爐屬於 SMT 產線的迴焊段"},
                ],
            }
        )
    )

    (card,) = extract_cards(model, document)

    assert [s.text for s in card.statements] == ["是一種加熱設備"]


def test_a_claim_with_no_quote_at_all_is_dropped():
    """The empty string is a substring of every document, so "the quote must
    appear in the text" waves through a claim that quoted nothing — the one
    shape a model reaches for when it has a claim it cannot source."""
    document = "本廠使用回焊爐進行焊接。"
    model = _Model(
        _reply(
            {
                "term": "回焊爐",
                "keys": ["回焊爐"],
                "statements": [{"text": "是薔薇科的一種", "quote": ""}],
            }
        )
    )

    assert extract_cards(model, document) == []


def test_a_reply_that_is_not_usable_costs_this_document_and_nothing_else():
    """One document out of hundreds answers with prose, or with truncated JSON.
    Raising would end the run; returning nothing loses that document only."""
    replies = (
        "I could not find any terms.",  # no JSON at all
        '{"cards": [',  # braces never closed
        '{"cards": [}',  # braces closed, contents unparseable
        '{"cards": "not a list"}',  # parses, wrong shape
    )
    for reply in replies:
        assert extract_cards(_Model(reply), "回焊爐是一種加熱設備。") == [], reply


def test_entries_that_are_not_cards_are_skipped_rather_than_fatal():
    """Small models put strings and half-filled objects in the array they were
    asked to fill with cards."""
    model = _Model(json.dumps({"cards": ["回焊爐", {"keys": ["x"]}, {"term": "  "}]}))

    assert extract_cards(model, "回焊爐是一種加熱設備。") == []


def test_the_prompt_handed_out_to_edit_is_the_one_that_runs():
    """`--dump-prompts` exists so a person starts from the real thing."""
    from workspace_app.kb.cards.extract import built_in_prompt

    model = _Model('{"cards": []}')
    extract_cards(model, "回焊爐")

    assert model.prompts[0] == built_in_prompt().replace("{text}", "回焊爐")
