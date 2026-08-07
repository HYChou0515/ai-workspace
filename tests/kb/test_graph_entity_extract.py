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
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
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


def test_one_pass_also_yields_what_the_passage_states_about_those_things():
    """#630 P4 — attributes ride in the SAME extraction as the mentions.

    They used to be a second prompt over the same chunk, which doubled the model
    time and, worse, split the signal: the pass that decided what the passage
    talks about and the pass that decided what it says about them never saw each
    other's answers, so a subject could be named in one and unknown in the other.
    """
    from workspace_app.kb.graph.entity_extract import AttributeClaim

    llm = _FakeLlm(
        '{"mentions": [{"surface": "回焊爐", "kind": "機台"}],'
        ' "aliases": [], "relationships": [],'
        ' "attributes": [{"subject": "回焊爐", "attribute": "良率",'
        ' "value": "98.7", "unit": "%", "period": "Q3"},'
        ' {"subject": "回焊爐", "attribute": "recipe", "value": "PPOOIXUX"}]}'
    )
    got = extract_entities(llm, "回焊爐 Q3 良率 98.7%,recipe 是 PPOOIXUX。")
    assert got.mentions == [EntityMention(surface="回焊爐", kind="機台")]
    assert got.attributes == [
        AttributeClaim(subject="回焊爐", attribute="良率", value="98.7", unit="%", period="Q3"),
        AttributeClaim(subject="回焊爐", attribute="recipe", value="PPOOIXUX"),
    ]


def test_a_reply_without_attributes_is_still_usable():
    """Small models drift back to the shape they were asked for last time; a
    missing key must mean 'none', not 'drop the passage'."""
    llm = _FakeLlm('{"mentions": [{"surface": "爐"}], "aliases": [], "relationships": []}')
    got = extract_entities(llm, "爐")
    assert got.mentions == [EntityMention(surface="爐", kind="")]
    assert got.attributes == []


def test_an_attribute_missing_a_part_is_dropped():
    llm = _FakeLlm(
        '{"mentions": [], "aliases": [], "relationships": [],'
        ' "attributes": [{"subject": "", "attribute": "良率", "value": "5"},'
        ' {"subject": "爐", "attribute": "", "value": "5"},'
        ' {"subject": "爐", "attribute": "良率", "value": ""},'
        ' {"subject": "爐", "attribute": "良率", "value": "98"}]}'
    )
    from workspace_app.kb.graph.entity_extract import AttributeClaim

    assert extract_entities(llm, "t").attributes == [
        AttributeClaim(subject="爐", attribute="良率", value="98")
    ]


def test_the_prompt_never_gates_on_the_value_being_a_number():
    """The guard on the gate itself — the regression #630 exists to prevent.

    The old attribute prompt opened with "every metric that carries a NUMERIC
    value", which is what made a stated recipe or supplier unrepresentable. This
    test fails the moment that word comes back."""
    llm = _FakeLlm("{}")
    extract_entities(llm, "t")
    prompt = llm.prompts[0].lower()
    assert "numeric" not in prompt
    # …and it says the opposite out loud: any value, kept exactly as written.
    # ("part numbers" legitimately appears in the mention section — the word to
    # watch is the one that GATES on the value's type, not the word "number".)
    assert "verbatim" in prompt


# ── the corpus's own criterion ────────────────────────────────────────
#
# "anything a reader would consider a distinct thing" is not a criterion: every
# noun passes it. A capable model follows it exactly and exhaustively, so the
# stronger the model the more the vocabulary fills with 「系統」/「問題」/「資料」
# and with everything a table happened to label. Nothing downstream ever
# disagrees — the extractor drops only empty surfaces, the writer keeps every
# distinct key, and the vocabulary mints an identity per key.
#
# The missing piece is a statement of what deserves to be a thing HERE, and that
# is domain knowledge: a manufacturing corpus wants machines, processes,
# materials, defects, parameters and part numbers, while the same words are
# noise in a finance corpus. Every criterion invented from outside a corpus in
# this module has been wrong within a corpus or two, so it is not invented — it
# is supplied by whoever owns the collection, and these tests fix how it travels.


def test_the_corpus_guidance_reaches_the_model_verbatim():
    """Whatever the owner wrote is what the model is told — not a paraphrase."""
    llm = _FakeLlm("{}")
    extract_entities(llm, "t", guidance="只收機台、製程、缺陷、參數與料號。")
    assert "只收機台、製程、缺陷、參數與料號。" in llm.prompts[0]


def test_the_guidance_overrides_the_general_description():
    """It has to WIN, and be told to.

    Guidance that merely sits beside "anything a reader would consider a distinct
    thing" leaves the model with two rules and no way to choose, and the general
    one is the permissive one — so the block says out loud which governs, and it
    comes after the sentence it qualifies rather than before it.
    """
    llm = _FakeLlm("{}")
    extract_entities(llm, "t", guidance="ONLY machines.")
    prompt = llm.prompts[0]
    assert prompt.index("distinct thing") < prompt.index("ONLY machines.")
    assert prompt.index("ONLY machines.") < prompt.index("For each, give")
    assert "follow this" in prompt.lower()


def test_no_guidance_leaves_the_prompt_exactly_as_it_was():
    """A collection that never set one must behave identically to today.

    The knob ships with nothing in it, so its OFF state is the state every
    existing collection is in: empty guidance must not leave scaffolding, an
    empty heading, or a blank the model will try to interpret.
    """
    plain, blank = _FakeLlm("{}"), _FakeLlm("{}")
    extract_entities(plain, "t")
    extract_entities(blank, "t", guidance="   \n  ")
    assert plain.prompts[0] == blank.prompts[0]
    assert "follow this" not in plain.prompts[0].lower()


def test_guidance_does_not_disturb_the_passage_or_the_json_contract():
    """The block is inserted, not spliced through the rest of the prompt."""
    llm = _FakeLlm("{}")
    extract_entities(llm, "回焊爐溫度 245°C", guidance="ONLY machines.")
    prompt = llm.prompts[0]
    assert prompt.endswith("Passage:\n回焊爐溫度 245°C")
    assert '{"mentions": [{"surface": ..., "kind": ...}]' in prompt


def test_a_list_entry_that_is_not_an_object_is_skipped_not_fatal():
    """Every one of the four lists is written by a model, and a model that drifts
    puts a bare string where an object belongs. One malformed entry must cost the
    entry, not the passage — and never the batch the passage rides in."""
    llm = _FakeLlm(
        '{"mentions": ["回焊爐", {"surface": "SPI", "kind": "機台"}],'
        ' "aliases": ["nope", {"a": "SPI", "b": "錫膏檢查機", "quote": "SPI(錫膏檢查機)"}],'
        ' "relationships": [42, {"subject": "SPI", "predicate": "檢查", "object": "錫膏"}],'
        ' "attributes": [null, {"subject": "SPI", "attribute": "良率", "value": "99"}]}'
    )
    got = extract_entities(llm, "SPI(錫膏檢查機)")
    assert [m.surface for m in got.mentions] == ["SPI"]
    assert [a.b for a in got.aliases] == ["錫膏檢查機"]
    assert [r.predicate for r in got.relationships] == ["檢查"]
    assert [c.attribute for c in got.attributes] == ["良率"]


# ── the prompt itself, as something a person edits ───────────────────
#
# The criterion cannot win an argument with the sentence it is appended to. The
# prompt opens with "List everything the passage below talks about — anything a
# reader would consider a distinct thing", and a corpus's real answer to "what
# deserves to exist here" often contradicts it outright. So the whole prompt has
# to be replaceable, not just extendable — and replaceable from a FILE, because
# writing a good one takes many passes and nobody does many passes through a
# Python constant.


class _Recorder(ILlm):
    """Records how `collect` was called, not only what it was asked."""

    def __init__(self, reply: str = "{}") -> None:
        self._reply = reply
        self.kwargs: dict = {}

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield self._reply, False

    def collect(self, prompt: str, on_chunk=None, **kw) -> str:  # noqa: ANN001
        self.kwargs = kw
        return self._reply


def test_the_extractor_recovers_a_reply_that_landed_in_the_reasoning_channel():
    """A reasoning model that runs out of tokens before closing `</think>` puts
    its WHOLE reply — the JSON this has to parse — in `reasoning_content`, and no
    content delta ever arrives. `collect` then returns "" and this reports "no
    usable JSON", which reads like a model that answered badly rather than one
    whose answer went down the other pipe.

    Production runs reasoning models. Three other structured callers already ask
    for the recovery (#494); this one did not, and a run of it dropped batch
    after batch for half an hour saying only that.
    """
    llm = _Recorder()
    extract_entities(llm, "t")
    assert llm.kwargs.get("recover_reasoning") is True


def test_an_unparseable_reply_is_logged_with_enough_of_itself_to_diagnose(caplog):
    """ "No usable JSON" without the reply names the symptom and withholds the
    evidence — a refusal, a think block and a truncated generation all produce
    that one line, and they need different fixes."""
    import logging

    llm = _FakeLlm("I am sorry, I cannot help with that request.")
    with caplog.at_level(logging.WARNING, logger="workspace_app.kb.graph.entity_extract"):
        extract_entities(llm, "t")

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "I am sorry" in logged, f"the reply itself never reached the log: {logged!r}"


def test_a_supplied_prompt_replaces_the_built_in_one():
    llm = _FakeLlm("{}")
    extract_entities(llm, "回焊爐 245°C", prompt="Name only the machines.\n\n{text}")
    assert llm.prompts[0] == "Name only the machines.\n\n回焊爐 245°C"
    assert "distinct thing" not in llm.prompts[0]


def test_a_supplied_prompt_still_takes_the_corpus_criterion():
    llm = _FakeLlm("{}")
    extract_entities(llm, "t", prompt="Base.\n{guidance}\n{text}", guidance="只收機台。")
    assert "只收機台。" in llm.prompts[0]


def test_a_prompt_with_nowhere_to_put_the_passage_is_refused():
    """Loudly, and before the model is called. Without `{text}` every passage
    extracts to nothing, and `extract_entities` never raises — so the run would
    finish, report zero, and read exactly like a corpus that mentions nothing."""
    import pytest

    llm = _FakeLlm("{}")
    with pytest.raises(ValueError, match="text"):
        extract_entities(llm, "t", prompt="Name only the machines.")
    assert llm.prompts == []
