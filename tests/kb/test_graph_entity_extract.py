"""#534 B — pull the things a passage talks about, verbatim.

The extractor's only job is to say WHAT the text mentions and, in the text's own
words, what kind of thing it is. It does not decide whether two mentions are the
same thing, does not normalise, and does not filter by kind — every one of those
is a later, separate decision made against accumulated evidence rather than one
passage at a time.

Two consequences show up in these tests. The surface is kept EXACTLY as written,
because the raw form is the evidence and everything downstream derives from it.
And the kind is free text, not a fixed list: the useful kinds are domain-specific
(a manufacturing corpus wants 機台 / 製程 / 缺陷, not the categories a general
model would guess), so the taxonomy has to come out of the data rather than be
imposed on it — the kind labels get unified by the same mechanism as everything
else.
"""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.graph.entity_extract import (
    DeclaredAlias,
    EntityMention,
    StatedRelationship,
    extract_entities,
)
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield self._reply, False


def test_extracts_surface_and_kind():
    llm = _FakeLlm(
        '[{"surface": "回焊爐", "kind": "機台"}, {"surface": "錫膏印刷", "kind": "製程"}]'
    )
    assert extract_entities(llm, "…").mentions == [
        EntityMention(surface="回焊爐", kind="機台"),
        EntityMention(surface="錫膏印刷", kind="製程"),
    ]


def test_the_surface_is_kept_verbatim():
    """No normalisation here. The raw form IS the evidence — every key downstream
    is derived from it, and a normalisation baked in at extraction could never be
    revised without re-running the model."""
    llm = _FakeLlm('[{"surface": "  Reflow Oven (RO-3) ", "kind": "tool"}]')
    (got,) = extract_entities(llm, "…").mentions
    assert got.surface == "Reflow Oven (RO-3)"  # only the outer whitespace goes


def test_the_kind_is_free_text_not_a_fixed_list():
    """ "機台" and "tool" are the same kind written two ways, and that is fine here
    — unifying them is the vocabulary's job, done against all the evidence, not a
    choice forced on the model one passage at a time."""
    llm = _FakeLlm('[{"surface": "RO-3", "kind": "tool"}, {"surface": "SPI", "kind": "設備"}]')
    assert [m.kind for m in extract_entities(llm, "…").mentions] == ["tool", "設備"]


def test_an_entry_without_a_surface_is_dropped():
    """A kind with nothing to attach it to is not a mention of anything."""
    llm = _FakeLlm('[{"surface": "", "kind": "機台"}, {"kind": "製程"}, {"surface": "SPI"}]')
    assert extract_entities(llm, "…").mentions == [EntityMention(surface="SPI", kind="")]


def test_a_repeated_surface_is_returned_twice():
    """Counting occurrences is the writer's job — it aggregates across the whole
    document, so the extractor must not silently collapse them here and lose the
    signal."""
    llm = _FakeLlm('[{"surface": "RO-3", "kind": "機台"}, {"surface": "RO-3", "kind": "tool"}]')
    assert len(extract_entities(llm, "…").mentions) == 2


def test_a_reply_with_no_json_array_yields_nothing():
    """Never raises: one unparseable passage must not fail the batch it rides in."""
    assert extract_entities(_FakeLlm("I could not find any entities."), "…").mentions == []


def test_a_malformed_json_array_yields_nothing():
    assert extract_entities(_FakeLlm('[{"surface": "RO-3",]'), "…").mentions == []


def test_a_json_object_instead_of_an_array_yields_nothing():
    assert extract_entities(_FakeLlm('{"surface": "RO-3"}'), "…").mentions == []


def test_prose_around_the_array_is_tolerated():
    """Small models wrap JSON in commentary; the batch should survive it."""
    llm = _FakeLlm('Sure! Here you go:\n[{"surface": "SPI", "kind": "機台"}]\nHope that helps.')
    assert extract_entities(llm, "…").mentions == [EntityMention(surface="SPI", kind="機台")]


class TestDeclaredAliases:
    """An equivalence the PASSAGE states — "回焊爐,以下簡稱 RO" — reported by the
    model rather than judged by it.

    That distinction is the whole basis for letting it apply without review. Both
    come out of the same model, but one points at a sentence anyone can go and
    read and the other points at nothing outside the model's own impression. What
    keeps the difference honest is a requirement, not a label: the extractor must
    QUOTE the words that declare it. A declaration that cannot be quoted is not a
    declaration — it is an opinion, and it waits for a person like every other
    opinion does.
    """

    def test_a_quoted_declaration_is_returned(self):
        llm = _FakeLlm(
            '{"mentions": [{"surface": "回焊爐", "kind": "機台"}],'
            ' "aliases": [{"a": "回焊爐", "b": "RO", "quote": "回焊爐,以下簡稱 RO"}]}'
        )
        got = extract_entities(llm, "產線使用回焊爐,以下簡稱 RO,溫度 250°C")
        assert got.aliases == [DeclaredAlias(a="回焊爐", b="RO", quote="回焊爐,以下簡稱 RO")]

    def test_a_declaration_without_a_quote_is_dropped(self):
        """Not demoted to a weaker basis here — simply not a declaration. The
        model is free to propose it again through the path that expects an
        opinion, where a person will look at it."""
        llm = _FakeLlm('{"mentions": [], "aliases": [{"a": "回焊爐", "b": "RO", "quote": ""}]}')
        assert extract_entities(llm, "回焊爐,以下簡稱 RO").aliases == []

    def test_a_quote_that_is_not_in_the_passage_is_dropped(self):
        """The quote has to be checkable against the text it came from, or the
        requirement is decoration: a model that can invent the sentence too has
        given nothing a person could verify."""
        llm = _FakeLlm(
            '{"mentions": [], "aliases": [{"a": "回焊爐", "b": "RO",'
            ' "quote": "this sentence is not in the passage"}]}'
        )
        assert extract_entities(llm, "回焊爐是一種設備").aliases == []

    def test_an_incomplete_pair_is_dropped(self):
        llm = _FakeLlm('{"mentions": [], "aliases": [{"a": "回焊爐", "quote": "回焊爐"}]}')
        assert extract_entities(llm, "回焊爐").aliases == []

    def test_the_old_bare_array_reply_still_yields_mentions(self):
        """Small models drift back to the simpler shape they were asked for last
        time; a reply that is just the mention array must still work."""
        got = extract_entities(_FakeLlm('[{"surface": "SPI", "kind": "機台"}]'), "…")
        assert got.mentions == [EntityMention(surface="SPI", kind="機台")]
        assert got.aliases == []


class TestRelationships:
    """What the passage says CONNECTS two things — the third leg, and the one that
    makes this a graph rather than a list.

    It rides the SAME call as the mentions. The issue asked for joint extraction
    for two reasons and both hold here: one pass instead of two on the scarcest
    resource this feature has, and the connection is stated in the same sentence
    that names its ends, so splitting them throws away the association and asks a
    second model to guess it back.
    """

    def test_a_stated_relationship_is_returned(self):
        llm = _FakeLlm(
            '{"mentions": [], "aliases": [], "relationships": ['
            '{"subject": "回焊爐", "predicate": "造成", "object": "空洞",'
            ' "quote": "回焊爐溫度過高造成空洞"}]}'
        )
        got = extract_entities(llm, "回焊爐溫度過高造成空洞")
        assert got.relationships == [
            StatedRelationship(
                subject="回焊爐", predicate="造成", object="空洞", quote="回焊爐溫度過高造成空洞"
            )
        ]

    def test_the_predicate_is_free_text(self):
        """ "造成" and "leads to" are one predicate written two ways, and that is
        fine here — the predicates are unified by the same mechanism as everything
        else, so the vocabulary of connections comes out of the corpus instead of
        a list someone outside it wrote in advance."""
        llm = _FakeLlm(
            '{"relationships": [{"subject": "A", "predicate": "leads to", "object": "B"},'
            ' {"subject": "C", "predicate": "造成", "object": "D"}]}'
        )
        assert [r.predicate for r in extract_entities(llm, "…").relationships] == [
            "leads to",
            "造成",
        ]

    def test_an_incomplete_relationship_is_dropped(self):
        """A connection missing an end connects nothing."""
        llm = _FakeLlm(
            '{"relationships": [{"subject": "A", "predicate": "造成"},'
            ' {"predicate": "造成", "object": "B"},'
            ' {"subject": "A", "object": "B"}]}'
        )
        assert extract_entities(llm, "…").relationships == []

    def test_a_relationship_needs_no_quote(self):
        """Unlike an alias. An alias is APPLIED without review, so it has to point
        at a sentence; a relationship is evidence like a mention, and its
        provenance is the chunk it was read from — already recorded."""
        llm = _FakeLlm('{"relationships": [{"subject": "A", "predicate": "造成", "object": "B"}]}')
        (got,) = extract_entities(llm, "…").relationships
        assert got.quote == ""
