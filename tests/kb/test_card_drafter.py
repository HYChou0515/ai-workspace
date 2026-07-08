"""LlmCardDrafter (#175) — the LLM half of "自動 context card". A fake ``ILlm``
yields a canned response so the prompt-building + tolerant JSON parse are pinned
without a model."""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.card_drafter import LlmCardDrafter, NullCardDrafter, drafting_prompt
from workspace_app.kb.llm import ILlm


def test_null_drafter_proposes_nothing():
    # The no-LLM-configured drafter: the feature stays mounted but every doc
    # digests to an empty result (no cards, no questions).
    d = NullCardDrafter().digest(doc_path="a.md", doc_text="anything")
    assert (d.cards, d.term_questions, d.description_questions) == ([], [], [])


class _FakeLlm(ILlm):
    """collect() returns a canned response; records every prompt it was sent."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield (self._response, False)


_GOOD = json.dumps(
    {
        "cards": [
            {
                "title": "Metal 4",
                "keys": ["M4", "Metal 4"],
                "body": "The fourth metal layer.",
                "confident": True,
                "snippet": "M4 (Metal 4) is the fourth interconnect layer.",
            }
        ]
    }
)


def test_digest_parses_cards_and_both_question_kinds():
    # #377: the SAME LLM pass that drafts cards also raises the terms it can't
    # define (→ card) and the passages it can't follow (→ wiki), in one response.
    raw = json.dumps(
        {
            "cards": [
                {
                    "keys": ["M4"],
                    "title": "Metal 4",
                    "body": "The fourth metal layer.",
                    "snippet": "s",
                }
            ],
            "term_questions": [{"term": "R7", "question": "What is the R7 recipe?"}],
            "description_questions": [
                {"quote": "uses M4 then CMP", "question": "Why skip the clean before CMP?"}
            ],
        }
    )
    d = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...")
    assert [c.keys for c in d.cards] == [["M4"]]
    assert [(q.term, q.question) for q in d.term_questions] == [("R7", "What is the R7 recipe?")]
    assert [(q.quote, q.question) for q in d.description_questions] == [
        ("uses M4 then CMP", "Why skip the clean before CMP?")
    ]


def test_parses_a_well_formed_response_into_card_drafts():
    drafter = LlmCardDrafter(_FakeLlm(_GOOD))
    (card,) = drafter.digest(doc_path="a.md", doc_text="...").cards
    assert card.title == "Metal 4"
    assert card.keys == ["M4", "Metal 4"]
    assert card.body == "The fourth metal layer."
    assert card.confident is True
    assert card.snippet == "M4 (Metal 4) is the fourth interconnect layer."


def test_tolerates_a_fenced_or_prefixed_response():
    fenced = "Sure! Here are the cards:\n```json\n" + _GOOD + "\n```"
    (card,) = LlmCardDrafter(_FakeLlm(fenced)).digest(doc_path="a.md", doc_text="...").cards
    assert card.keys == ["M4", "Metal 4"]


def test_an_unparseable_response_yields_no_cards_and_warns(caplog):
    with caplog.at_level("WARNING"):
        d = LlmCardDrafter(_FakeLlm("I could not find any terms.")).digest(
            doc_path="a.md", doc_text="..."
        )
    assert d.cards == []
    # #494: the failure must be visible with the doc + a prefix of the raw reply,
    # not a silent empty run.
    rec = next(r for r in caplog.records if "no parseable" in r.message.lower())
    assert "a.md" in rec.message


def test_ignores_a_think_block_and_parses_the_real_object_after_it():
    # #494: a reasoning model may draft SCRATCH json inside <think>…</think>, then
    # emit the real answer. The naive first-"{" extractor grabbed the scratch; we
    # must strip the think span and parse the real object.
    raw = '<think>let me draft {"cards": [{"keys": ["SCRATCH"], "title": "wrong"}]}</think>' + _GOOD
    (card,) = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert card.keys == ["M4", "Metal 4"]  # the REAL card, not the scratch


def test_recovers_json_from_unterminated_reasoning():
    # #494 recovery path: collect() hands back the reasoning text (the answer that
    # landed in the reasoning channel), which has an UNTERMINATED <think> — the
    # JSON must still be found, not stripped away.
    raw = "<think>the answer is " + _GOOD
    (card,) = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert card.keys == ["M4", "Metal 4"]


def test_skips_a_preamble_brace_that_is_not_a_digest():
    # A non-digest object before the real one (a stray {...} in prose) must not be
    # mistaken for the answer — pick the object that actually has digest keys.
    raw = 'Here is my reasoning: {"note": "thinking"} and the result:\n' + _GOOD
    (card,) = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert card.keys == ["M4", "Metal 4"]


def test_a_brace_inside_a_string_does_not_break_extraction():
    # The balanced scan is string-aware: a "}" inside a JSON string value must not
    # be counted as the object's close, and an escaped quote (\") must not end the
    # string early. json.dumps encodes both here.
    raw = json.dumps(
        {"cards": [{"keys": ["X"], "title": "t", "body": 'he said "}" today', "snippet": "s"}]}
    )
    (card,) = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert card.body == 'he said "}" today'


def test_a_parseable_but_non_digest_object_falls_back_to_an_empty_digest(caplog):
    # No object carries digest keys, but one parses — we fall back to it rather
    # than raising; it maps to an empty digest (and warns), never a crash.
    raw = json.dumps({"result": "I found nothing worth carding"})
    with caplog.at_level("WARNING"):
        d = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="x.md", doc_text="...")
    assert (d.cards, d.term_questions, d.description_questions) == ([], [], [])
    assert any("empty" in r.message.lower() for r in caplog.records)


def test_extraction_skips_a_malformed_object_and_keeps_the_first_fallback():
    # A balanced but INVALID {…} is skipped (not a crash); of two parseable
    # non-digest objects the FIRST is kept as the fallback. Exercises the
    # malformed-skip and the "fallback already set" branches.
    raw = '{ this is not json } {"a": 1} {"b": 2}'
    d = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="m.md", doc_text="...")
    assert (d.cards, d.term_questions, d.description_questions) == ([], [], [])


def test_a_parsed_but_empty_digest_warns(caplog):
    # #494: the model answered with a well-formed but EMPTY digest (or a shape we
    # can't map) — a green run that produced nothing must say so, tied to the doc.
    raw = json.dumps({"cards": [], "term_questions": [], "description_questions": []})
    with caplog.at_level("WARNING"):
        d = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="empty.md", doc_text="...")
    assert (d.cards, d.term_questions, d.description_questions) == ([], [], [])
    assert any("empty" in r.message.lower() and "empty.md" in r.message for r in caplog.records)


def test_cards_missing_keys_or_with_wrong_types_are_dropped():
    raw = json.dumps(
        {
            "cards": [
                {"title": "no keys", "body": "x"},  # missing keys
                {"keys": "M4", "body": "x"},  # keys not a list
                {"keys": ["  "], "title": "blank key"},  # no usable key after strip
                {"keys": ["OK"], "title": "good", "body": "y", "snippet": "s"},
            ]
        }
    )
    cards = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert [c.keys for c in cards] == [["OK"]]


def test_confidence_defaults_to_true_when_absent():
    raw = json.dumps({"cards": [{"keys": ["X"], "title": "X", "body": "b", "snippet": "s"}]})
    (card,) = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert card.confident is True


def test_an_uncertain_card_is_kept_with_its_flag():
    raw = json.dumps(
        {"cards": [{"keys": ["X"], "title": "X", "body": "?", "snippet": "s", "confident": False}]}
    )
    (card,) = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert card.confident is False


def test_the_number_of_cards_is_capped():
    raw = json.dumps({"cards": [{"keys": [f"K{i}"], "title": f"t{i}"} for i in range(10)]})
    cards = LlmCardDrafter(_FakeLlm(raw), max_cards=3).digest(doc_path="a.md", doc_text="...").cards
    assert len(cards) == 3


def test_the_prompt_carries_the_document_text_and_path():
    llm = _FakeLlm(_GOOD)
    LlmCardDrafter(llm).digest(doc_path="reflow-spec.md", doc_text="Zone 3 setpoint 245C.")
    (prompt,) = llm.prompts
    assert "Zone 3 setpoint 245C." in prompt
    assert "reflow-spec.md" in prompt


def test_drafting_prompt_leaves_the_json_example_intact():
    """str.replace (not .format) — the template's literal JSON braces survive."""
    prompt = drafting_prompt("doc body", doc_path="p.md")
    assert '{"cards":' in prompt
    assert "doc body" in prompt
    assert "p.md" in prompt


def test_a_cards_value_that_is_not_a_list_yields_nothing():
    assert (
        LlmCardDrafter(_FakeLlm(json.dumps({"cards": "nope"})))
        .digest(doc_path="a.md", doc_text="...")
        .cards
        == []
    )


def test_non_dict_items_are_skipped():
    raw = json.dumps({"cards": ["just a string", {"keys": ["OK"], "title": "t"}]})
    cards = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards
    assert [c.keys for c in cards] == [["OK"]]


def test_items_with_a_wrong_typed_field_are_dropped():
    raw = json.dumps({"cards": [{"keys": ["X"], "title": 5}]})  # title not a string
    assert LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").cards == []


def test_an_unterminated_json_object_yields_nothing():
    assert (
        LlmCardDrafter(_FakeLlm('{"cards": [')).digest(doc_path="a.md", doc_text="...").cards == []
    )


def test_malformed_term_questions_are_dropped():
    # not-a-list → none; then per-item drops: non-dict, blank term, wrong-typed term.
    assert (
        LlmCardDrafter(_FakeLlm(json.dumps({"term_questions": "nope"})))
        .digest(doc_path="a.md", doc_text="...")
        .term_questions
        == []
    )
    raw = json.dumps(
        {
            "term_questions": [
                "just a string",  # not a dict
                {"term": "   ", "question": "blank term"},  # blank after strip
                {"term": 5, "question": "wrong type"},  # term not a string
                {"term": "R7", "question": "kept"},  # the one good item
            ]
        }
    )
    qs = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").term_questions
    assert [(q.term, q.question) for q in qs] == [("R7", "kept")]


def test_malformed_description_questions_are_dropped():
    assert (
        LlmCardDrafter(_FakeLlm(json.dumps({"description_questions": "nope"})))
        .digest(doc_path="a.md", doc_text="...")
        .description_questions
        == []
    )
    raw = json.dumps(
        {
            "description_questions": [
                "just a string",  # not a dict
                {"quote": "  ", "question": "blank quote"},  # blank after strip
                {"quote": 9, "question": "wrong type"},  # quote not a string
                {"quote": "uses M4 then CMP", "question": "kept"},  # the one good item
            ]
        }
    )
    qs = LlmCardDrafter(_FakeLlm(raw)).digest(doc_path="a.md", doc_text="...").description_questions
    assert [(q.quote, q.question) for q in qs] == [("uses M4 then CMP", "kept")]
