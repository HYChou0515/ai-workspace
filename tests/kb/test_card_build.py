"""One card per term, its body derived from every statement the corpus makes.

The failure this replaces: a body authored per document, then a winner picked
between them. No document knows what the others say, so every facet but one is
discarded — "蘋果是水果" and "蘋果是紅色" arrive from different documents and the
second is dropped on the floor. Deriving the body from the accumulated
statements makes the merge the normal case rather than a special one, and makes
it re-runnable: another document arrives, the body is recomputed, nothing is
overwritten because nothing was ever stored as the answer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from workspace_app.kb.cards.build import DocSource, build_cards
from workspace_app.kb.llm import ILlm

_SYNTHESIS_MARK = "STATEMENTS"


class _Corpus(ILlm):
    """Extracts per document by keyword; synthesises by joining what it is given."""

    def __init__(self, per_document: dict[str, list[dict]]) -> None:
        self._per_document = per_document
        self.synthesis_prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if _SYNTHESIS_MARK in prompt:
            self.synthesis_prompts.append(prompt)
            yield json.dumps({"title": "蘋果", "body": "蘋果是紅色的水果"}), False
            return
        for marker, cards in self._per_document.items():
            if marker in prompt:
                yield json.dumps({"cards": cards}, ensure_ascii=False), False
                return
        yield '{"cards": []}', False


def _card(term: str, keys: list[str], claim: str, quote: str) -> dict:
    return {"term": term, "keys": keys, "statements": [{"text": claim, "quote": quote}]}


def test_two_documents_about_one_term_make_one_card_carrying_both_statements():
    docs = [
        DocSource(doc_id="a", text="蘋果是水果,常見於超市。"),
        DocSource(doc_id="b", text="蘋果是紅色,也有青色品種。"),
    ]
    model = _Corpus(
        {
            "蘋果是水果": [_card("蘋果", ["蘋果", "Apple"], "是水果", "蘋果是水果")],
            "蘋果是紅色": [_card("蘋果", ["蘋果"], "是紅色", "蘋果是紅色")],
        }
    )

    (card,) = build_cards(model, docs)

    assert sorted(s.text for s in card.statements) == ["是水果", "是紅色"]
    assert card.body == "蘋果是紅色的水果"
    assert card.sources == ["a", "b"], "the card must say which documents it rests on"


def test_the_synthesiser_is_handed_every_statement_with_its_quote():
    """It is asked to lose nothing and add nothing, and it can do neither unless
    it holds the whole set. The quotes travel too: a claim summarised twice from
    the same sentence should read as one thing, and only the source words show
    that."""
    docs = [
        DocSource(doc_id="a", text="蘋果是水果,常見於超市。"),
        DocSource(doc_id="b", text="蘋果是紅色,也有青色品種。"),
    ]
    model = _Corpus(
        {
            "蘋果是水果": [_card("蘋果", ["蘋果"], "是水果", "蘋果是水果")],
            "蘋果是紅色": [_card("蘋果", ["蘋果"], "是紅色", "蘋果是紅色")],
        }
    )

    build_cards(model, docs)

    (asked,) = model.synthesis_prompts
    assert "是水果" in asked and "是紅色" in asked
    assert "蘋果是水果" in asked and "蘋果是紅色" in asked, "the quotes never reached it"


def test_the_same_claim_from_two_documents_is_carried_once():
    """A corpus repeats itself constantly — the same sentence appears in a spec
    and in the deck that summarises it. Handing the synthesiser the claim twice
    invites it to write the fact twice."""
    docs = [
        DocSource(doc_id="a", text="蘋果是水果。"),
        DocSource(doc_id="b", text="蘋果是水果。"),
    ]
    model = _Corpus({"蘋果是水果": [_card("蘋果", ["蘋果"], "是水果", "蘋果是水果")]})

    (card,) = build_cards(model, docs)

    assert [s.text for s in card.statements] == ["是水果"]
    assert card.sources == ["a", "b"], "both documents still count as evidence"


class _Synth(_Corpus):
    """A corpus whose synthesis step answers with something unusable."""

    def __init__(self, reply: str) -> None:
        super().__init__({"蘋果是水果": [_card("蘋果", ["蘋果"], "是水果", "蘋果是水果")]})
        self._reply = reply

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if _SYNTHESIS_MARK in prompt:
            yield self._reply, False
        else:
            yield from super().stream(prompt)


def test_an_unusable_synthesis_leaves_the_evidence_intact():
    """The statements are the durable part; the body is derived from them and can
    be derived again. A synthesis that fails must not take the evidence with it —
    the card still records what the documents said and which ones they were."""
    docs = [DocSource(doc_id="a", text="蘋果是水果。")]

    (card,) = build_cards(_Synth("I am not sure how to combine these."), docs)

    assert card.body == ""
    assert [s.text for s in card.statements] == ["是水果"]
    assert card.title == "蘋果", "with no synthesised title the term itself still names the card"


def test_a_synthesis_reply_of_the_wrong_shape_is_read_as_no_body():
    docs = [DocSource(doc_id="a", text="蘋果是水果。")]

    for reply in ('{"title": ', '{"title": }', '{"nope": 1}', "no json here"):
        (card,) = build_cards(_Synth(reply), docs)
        assert card.body == "", reply


def test_the_synthesis_prompt_handed_out_to_edit_carries_both_slots():
    from workspace_app.kb.cards.build import built_in_synthesis_prompt

    assert "{term}" in built_in_synthesis_prompt()
    assert "{statements}" in built_in_synthesis_prompt()


def test_documents_are_read_several_at_a_time():
    """One model call per document IS the cost of a round, and the calls do not
    depend on each other. Serial was never a requirement — just nobody said
    otherwise, and a tuning loop is round-count times round-time."""
    import threading
    import time

    lock = threading.Lock()
    live = peak = 0

    class _Slow(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            nonlocal live, peak
            if _SYNTHESIS_MARK in prompt:
                yield json.dumps({"title": "t", "body": "b"}), False
                return
            with lock:
                live += 1
                peak = max(peak, live)
            try:
                time.sleep(0.05)
                yield '{"cards": []}', False
            finally:
                with lock:
                    live -= 1

    docs = [DocSource(doc_id=f"d{i}", text=f"文件 {i}") for i in range(8)]
    build_cards(_Slow(), docs, concurrency=4)

    assert peak > 1, "the documents were still read one at a time"
    assert peak <= 4, f"more calls were in flight ({peak}) than the limit allowed"


def test_the_documents_keep_their_order_however_they_finish():
    """Order decides which document a card names as its first source and the
    order statements are carried in. A run that reordered would produce a
    different glossary each time from the same corpus."""
    import time

    class _OutOfOrder(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            if _SYNTHESIS_MARK in prompt:
                yield json.dumps({"title": "t", "body": "b"}), False
                return
            index = int(prompt.rsplit("文件 ", 1)[-1])
            time.sleep(0.02 * (6 - index))  # later documents answer FIRST
            yield (
                json.dumps(
                    {
                        "cards": [
                            {
                                "term": f"w{index}",
                                "keys": [f"w{index}"],
                                "statements": [{"text": "x", "quote": f"文件 {index}"}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                False,
            )

    docs = [DocSource(doc_id=f"d{i}", text=f"文件 {i}") for i in range(6)]

    cards = build_cards(_OutOfOrder(), docs, concurrency=6)

    assert [c.statements[0].quote for c in cards] == [f"文件 {i}" for i in range(6)]
