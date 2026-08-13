"""A card's body is recomputed from its evidence, never overwritten.

The failure this replaces: a body authored per document and then chosen between.
「蘋果是水果」 arrives from one document and 「蘋果是紅色」 from another, and the
second overwrites — or spawns a sibling card — because nothing accumulates. Here
the statements accumulate on the card and the body is derived from all of them,
so a new document ADDS a clause instead of replacing an answer.
"""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.cards.accumulate import accumulate
from workspace_app.kb.cards.build import synthesise
from workspace_app.kb.llm import ILlm
from workspace_app.resources.kb import CardStatement


def _s(text: str, quote: str, doc: str = "d") -> CardStatement:
    return CardStatement(text=text, quote=quote, source_doc_id=doc)


def test_a_new_document_adds_to_what_the_card_already_knew():
    had = [_s("是水果", "蘋果是水果", "a")]

    got = accumulate(had, [_s("是紅色", "蘋果是紅色", "b")])

    assert [s.text for s in got] == ["是水果", "是紅色"]
    assert [s.source_doc_id for s in got] == ["a", "b"]


def test_the_same_claim_arriving_again_does_not_double_up():
    """A corpus repeats itself — a spec and the deck that summarises it say the
    same sentence. Carrying it twice invites the body to state the fact twice."""
    had = [_s("是水果", "蘋果是水果", "a")]

    got = accumulate(had, [_s("是水果", "蘋果是水果", "b")])

    assert [s.text for s in got] == ["是水果"]


def test_re_running_the_same_document_changes_nothing():
    """Card generation is triggered by hand and re-run over the same corpus all
    the time; that is exactly how the old pipeline grew several cards per term."""
    had = [_s("是水果", "蘋果是水果", "a"), _s("是紅色", "蘋果是紅色", "b")]

    assert accumulate(had, list(had)) == had


def test_the_body_is_written_from_every_statement_the_card_holds():
    class _Writer(ILlm):
        def __init__(self) -> None:
            self.asked: list[str] = []

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            self.asked.append(prompt)
            yield '{"title": "蘋果", "body": "蘋果是紅色的水果"}', False

    llm = _Writer()
    title, body = synthesise(
        llm, "蘋果", [_s("是水果", "蘋果是水果", "a"), _s("是紅色", "蘋果是紅色", "b")]
    )

    assert (title, body) == ("蘋果", "蘋果是紅色的水果")
    (asked,) = llm.asked
    assert "是水果" in asked and "是紅色" in asked
    assert "蘋果是水果" in asked, "the quotes never reached the writer"


def test_an_unusable_reply_leaves_the_evidence_intact():
    """The statements are the durable part; a body can always be derived again."""

    class _Mute(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield "not sure", False

    assert synthesise(_Mute(), "蘋果", [_s("是水果", "蘋果是水果")]) == ("", "")
