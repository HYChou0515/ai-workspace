"""#697 — chunk → graph as a computation, with no database in it.

The graph could not be tuned because running it had side effects: extraction
wipes and rewrites a document's rows, and the vocabulary pass builds identities
by querying the database while it creates them. So trying a different extraction
criterion meant mutating the corpus you were trying to judge, in an environment
that had data in it.

These tests fix the shape that removes the problem. The functions here take no
``SpecStar`` handle at all, so "it does not write" is not a promise anyone has to
keep — there is nothing to write with. Persistence becomes a separate step that
consumes what these produced, and the read-only inspection tool consumes the
same thing and dumps it as JSON. One core, two consumers.
"""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.graph.build import DocSource, build_doc_graph
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._replies[min(len(self.prompts) - 1, len(self._replies) - 1)], False


def test_a_documents_chunks_become_its_mentions_without_a_database():
    """The tracer bullet: text in, rows out, and no handle that could persist
    anything."""
    llm = _FakeLlm('{"mentions": [{"surface": "回焊爐", "kind": "機台"}]}')

    graph = build_doc_graph(
        llm,
        DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐 245°C")]),
        collection_id="col-1",
    )

    assert [(m.surface, m.kind) for m in graph.mentions] == [("回焊爐", "機台")]
    assert [m.source_doc_id for m in graph.mentions] == ["deck-A"]
    assert [m.collection_id for m in graph.mentions] == ["col-1"]


def test_the_statements_a_document_makes_become_claims():
    """The other half of the same answer. One model call per chunk feeds both
    layers, so the thing named and the fact stated about it can never disagree
    about which subject was meant."""
    llm = _FakeLlm(
        '{"mentions": [{"surface": "回焊爐", "kind": "機台"}],'
        ' "attributes": [{"subject": "回焊爐", "attribute": "良率",'
        ' "value": "98.7", "unit": "%", "period": "2024 Q3"}]}'
    )

    graph = build_doc_graph(
        llm,
        DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐良率 98.7%")]),
        collection_id="col-1",
    )

    (claim,) = graph.claims
    assert (claim.subject, claim.attribute, claim.value, claim.unit) == (
        "回焊爐",
        "良率",
        "98.7",
        "%",
    )
    # the derived keys ride along, by the same rules the stored rows use
    assert claim.norm_subject == "回焊爐"
    assert claim.norm_attribute == "良率"
    assert claim.norm_period == "Q:2024:3"
    assert claim.chunk_id == "c1"  # provenance back to the slide


def test_what_a_document_says_connects_two_things_becomes_a_relationship():
    """The leg that makes this a graph rather than a list. The ends stay as
    SURFACES — resolving them to identities is the vocabulary's job and changes as
    it learns, so freezing an id here would make this a second place identity
    lives, and two places drift."""
    llm = _FakeLlm(
        '{"mentions": [{"surface": "冷焊"}, {"surface": "回焊爐"}],'
        ' "relationships": [{"subject": "回焊爐", "predicate": "造成",'
        ' "object": "冷焊", "quote": "回焊爐溫度不足造成冷焊"}]}'
    )

    graph = build_doc_graph(
        llm,
        DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐溫度不足造成冷焊")]),
        collection_id="col-1",
    )

    (rel,) = graph.relationships
    assert (rel.subject, rel.predicate, rel.object) == ("回焊爐", "造成", "冷焊")
    assert rel.norm_predicate == "造成"
    assert rel.quote == "回焊爐溫度不足造成冷焊"  # it really is in the passage
    assert rel.chunk_id == "c1"


def test_an_equivalence_the_document_states_makes_both_names_mentions():
    """A declaration is worth nothing if only one side exists as evidence: "回焊爐,
    以下簡稱 RO" cannot be applied unless "RO" is also a row for the vocabulary to
    join. The passage said both names, so both are mentions of it, and the quote
    travels with the pair."""
    llm = _FakeLlm(
        '{"mentions": [{"surface": "回焊爐", "kind": "機台"}],'
        ' "aliases": [{"a": "回焊爐", "b": "RO", "quote": "回焊爐(以下簡稱 RO)"}]}'
    )

    graph = build_doc_graph(
        llm,
        DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐(以下簡稱 RO)")]),
        collection_id="col-1",
    )

    by_surface = {m.surface: m for m in graph.mentions}
    assert set(by_surface) == {"回焊爐", "RO"}
    assert by_surface["回焊爐"].declared_same_as == ["ro"]
    assert by_surface["RO"].declared_same_as == ["回焊爐"]
    assert by_surface["RO"].declared_quote == "回焊爐(以下簡稱 RO)"
    # the name the passage also listed keeps everything it had
    assert by_surface["回焊爐"].kind == "機台"


# ── the vocabulary, computed rather than queried ─────────────────────
#
# Deciding that two mentions name one thing needs ALL the evidence at once, so
# the pass used to read the whole corpus out of the store and create identities
# as it went — which is why it could not be re-run to see what a different
# extraction criterion would produce. Given the evidence as an argument it is an
# ordinary computation, and the same computation serves production and the
# read-only tool.


def _mention(
    doc: str,
    surface: str,
    *,
    collection: str = "c1",
    kind: str = "",
    occurrences: int = 1,
    same_as: list[str] | None = None,
    quote: str = "",
):
    from workspace_app.kb.graph.build import MentionEvidence
    from workspace_app.kb.graph.normalize import norm_surface
    from workspace_app.resources.graph import mention_id

    return MentionEvidence(
        id=mention_id(doc, surface),
        collection_id=collection,
        source_doc_id=doc,
        surface=surface,
        norm_surface=norm_surface(surface),
        kind=kind,
        norm_kind=norm_surface(kind),
        occurrences=occurrences,
        declared_same_as=tuple(same_as or ()),
        declared_quote=quote,
    )


def test_two_documents_naming_one_thing_share_one_identity():
    from workspace_app.kb.graph.build import build_vocabulary
    from workspace_app.resources.graph import mention_id

    vocab = build_vocabulary(
        [
            _mention("deck-A", "回焊爐", collection="c1", occurrences=5),
            # the same name typed loosely — CJK is not written with word spaces,
            # so this is noise the comparison key already removes
            _mention("deck-B", "回 焊 爐", collection="c2", occurrences=1),
        ],
        [],
    )

    (entity,) = vocab.entities.values()
    # the surface the corpus actually used most — a name someone wrote, never a key
    assert entity.canonical_name == "回焊爐"
    assert entity.collection_ids == ["c1", "c2"]  # evidence drives visibility
    assert {link.mention_id for link in vocab.links} == {
        mention_id("deck-A", "回焊爐"),
        mention_id("deck-B", "回 焊 爐"),
    }
    assert {link.basis for link in vocab.links} == {"identical"}


def test_a_kind_is_an_identity_like_anything_else():
    """「機台」 is a name for a sort of thing, and "tool" is the same name in
    another language — the same problem a thing has, so it gets the same answer
    rather than a mechanism of its own. The recursion stops there: a kind has no
    kind."""
    from workspace_app.kb.graph.build import build_vocabulary
    from workspace_app.kb.graph.normalize import norm_surface
    from workspace_app.resources.graph import entity_id

    vocab = build_vocabulary(
        [
            _mention("deck-A", "回焊爐", kind="機台", collection="c1"),
            _mention("deck-B", "SPI", kind="機台", collection="c2"),
        ],
        [],
    )

    by_name = {e.canonical_name: e for e in vocab.entities.values()}
    assert set(by_name) == {"回焊爐", "SPI", "機台"}
    kind = entity_id(norm_surface("機台"))
    assert by_name["回焊爐"].kind_id == kind
    assert by_name["SPI"].kind_id == kind
    assert by_name["機台"].kind_id == ""
    # A kind's evidence is the mentions carrying the label. Without it the kind
    # holds no collections, and the access scope reads "nothing vouches for this"
    # as invisible — so the kind would vanish from every page.
    assert by_name["機台"].collection_ids == ["c1", "c2"]


def _relationship(doc: str, subject: str, predicate: str, obj: str, *, collection: str = "c1"):
    from workspace_app.kb.graph.build import RelationEvidence
    from workspace_app.kb.graph.normalize import norm_surface

    del doc, subject, obj  # the vocabulary reads neither end, only the word
    return RelationEvidence(
        collection_id=collection,
        predicate=predicate,
        norm_predicate=norm_surface(predicate),
    )


def test_a_connection_word_gets_an_identity_too():
    """ "造成" and "leads to" are one connection written two ways — the same
    problem a thing has, so it gets the same answer. Once they are identities the
    grouping machinery joins them exactly as it joins anything else, and a page
    shows one predicate instead of two."""
    from workspace_app.kb.graph.build import build_vocabulary

    vocab = build_vocabulary(
        [],
        [
            _relationship("deck-A", "回焊爐", "造成", "冷焊", collection="c1"),
            _relationship("deck-B", "錫膏", "造成", "空洞", collection="c2"),
        ],
    )

    by_name = {e.canonical_name: e for e in vocab.entities.values()}
    assert set(by_name) == {"造成"}
    # a predicate's evidence is the relationships that use it, or the scope reads
    # "nothing vouches for this" and hides it from everyone, owner included
    assert by_name["造成"].collection_ids == ["c1", "c2"]


def test_an_equivalence_a_document_stated_is_applied_without_review():
    """Applied on its own, and the reason is not that a model produced it — a
    resemblance comes from the same model and waits for a person. It is that this
    one points at a SENTENCE: the link carries the quote, so what it rested on can
    be read by anyone who doubts it."""
    from workspace_app.kb.graph.build import build_vocabulary

    quote = "回焊爐(以下簡稱 RO)"
    vocab = build_vocabulary(
        [
            _mention("deck-A", "回焊爐", occurrences=9, same_as=["ro"], quote=quote),
            _mention("deck-A", "RO", occurrences=1, same_as=["回焊爐"], quote=quote),
        ],
        [],
    )

    live = [e for e in vocab.entities.values() if e.collection_ids]
    assert len(live) == 1, "the declaration was not applied"
    (host,) = live
    assert host.norm_keys == ["ro", "回焊爐"]  # both names now resolve here
    assert host.canonical_name == "回焊爐"  # the one the corpus leant on

    # Both mentions hang off it, and each link still says what it rested on: the
    # name that WAS the identity by its own key, and the one the sentence brought.
    by_basis = {link.basis: link for link in vocab.links}
    assert set(by_basis) == {"identical", "declared"}
    assert quote in by_basis["declared"].evidence


def test_the_model_can_only_propose_a_merge_never_apply_one():
    """The whole vocabulary goes to the model in one call rather than pair by
    pair: asking about pairs costs a call per pair, which is what forced the
    earlier attempts to invent a cheap test for which pairs deserved one, and
    every such test was wrong within a corpus or two. Given the list, the model
    also sees across languages, where no rule over characters ever could.

    What it produces is still only a PROPOSAL. That constraint is about who
    decides, not about how candidates are found — conflating the two is what
    produced the heuristics in the first place."""
    from workspace_app.kb.graph.build import build_vocabulary

    llm = _FakeLlm(
        '{"groups": [{"names": ["回焊爐", "Reflow Oven"],'
        ' "why": "the oven that melts solder paste"}]}'
    )

    vocab = build_vocabulary(
        [_mention("deck-A", "回焊爐"), _mention("deck-B", "Reflow Oven")],
        [],
        llm=llm,
    )

    # Still two identities: nothing was merged, because nothing was decided.
    assert len([e for e in vocab.entities.values() if e.collection_ids]) == 2
    pending = [link for link in vocab.links if link.state == "pending"]
    assert pending, "the model's grouping produced no proposal"
    assert {link.basis for link in pending} == {"resembles"}
    assert "melts solder paste" in pending[0].evidence  # what it is, so a person can check
    assert pending[0].proposed_from  # the identity this would absorb
    # and the active links are untouched
    assert len([link for link in vocab.links if link.state == "active"]) == 2


def test_without_a_model_no_proposals_are_made():
    """``llm=None`` means skip, so the pass can be turned off by configuration
    rather than by editing — and the read-only tool can run the deterministic
    part alone when what it wants to see is what the extractor did."""
    from workspace_app.kb.graph.build import build_vocabulary

    vocab = build_vocabulary([_mention("deck-A", "回焊爐"), _mention("deck-B", "Reflow Oven")], [])
    assert [link for link in vocab.links if link.state == "pending"] == []


# ── the whole thing, end to end ──────────────────────────────────────


def test_a_collection_becomes_a_graph_in_one_call():
    """What the read-only tool runs. Production reaches the same two functions in
    two stages with a store in between — because a corpus does not fit in one job
    — but the COMPUTATION is this one, so what the JSON shows is what production
    produces."""
    from workspace_app.kb.graph.build import build_graph

    llm = _FakeLlm(
        # both ends named as things, or the edge is a sentence parse and is dropped
        '{"mentions": [{"surface": "回焊爐", "kind": "機台"}, {"surface": "冷焊", "kind": "缺陷"}],'
        ' "relationships": [{"subject": "回焊爐", "predicate": "造成", "object": "冷焊"}]}',
        '{"mentions": [{"surface": "回 焊 爐", "kind": "機台"}]}',
    )

    graph = build_graph(
        llm,
        [
            DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐造成冷焊")]),
            DocSource(doc_id="deck-B", chunks=[("c2", "回 焊 爐")]),
        ],
        collection_id="col-1",
    )

    assert {m.source_doc_id for m in graph.mentions} == {"deck-A", "deck-B"}
    # both decks' spellings landed on ONE identity, and the kinds and the
    # connecting word are identities too
    names = {e.canonical_name for e in graph.entities.values()}
    assert names == {"回焊爐", "冷焊", "機台", "缺陷", "造成"}
    assert len([link for link in graph.links if link.basis == "identical"]) == 3


def test_the_same_evidence_twice_produces_the_same_graph():
    """Two runs must be byte-comparable or a JSON diff shows iteration order
    rather than the effect of the change being tried."""
    import msgspec

    from workspace_app.kb.graph.build import build_graph

    def run():
        llm = _FakeLlm(
            '{"mentions": [{"surface": "SPI", "kind": "設備"},'
            ' {"surface": "回焊爐", "kind": "機台"}]}'
        )
        return build_graph(
            llm,
            [DocSource(doc_id="deck-A", chunks=[("c1", "SPI 與回焊爐")])],
            collection_id="col-1",
        )

    assert msgspec.json.encode(run()) == msgspec.json.encode(run())


def test_a_things_kind_always_resolves_to_an_identity_that_still_holds_evidence():
    """A kind label can ALSO be a name some document declared equivalent to
    another. The declaration folds one key into the other and leaves a tombstone
    behind — and a tombstone holds no evidence, which the access scope reads as
    invisible to everyone. A kind pointing at one would vanish from every page
    that shows it, silently, and only for the corpora where a document happened
    to write 「機台,又稱設備」."""
    from workspace_app.kb.graph.build import build_vocabulary

    quote = "機台(又稱設備)"
    vocab = build_vocabulary(
        [
            # the label is the side the declaration ABSORBS (the host is the
            # smaller key, which is 機台 here) — so this is the ordering that
            # leaves the kind pointing at what is left behind
            _mention("deck-A", "回焊爐", kind="設備"),
            _mention("deck-B", "機台", same_as=["設備"], quote=quote),
            _mention("deck-B", "設備", same_as=["機台"], quote=quote),
        ],
        [],
    )

    thing = next(e for e in vocab.entities.values() if e.canonical_name == "回焊爐")
    assert thing.kind_id, "the thing lost its kind"
    kind = vocab.entities[thing.kind_id]
    assert kind.collection_ids, "the kind resolves to a tombstone — invisible on every page"
    assert not kind.merged_into


def test_a_declaration_naming_something_no_document_mentioned_is_ignored():
    """Nothing vouches for that name, so there is nothing to join — and minting an
    identity for it would put a name in the vocabulary no document ever said."""
    from workspace_app.kb.graph.build import build_vocabulary

    vocab = build_vocabulary(
        [_mention("deck-A", "回焊爐", same_as=["熱風爐"], quote="回焊爐,又稱熱風爐")], []
    )

    assert [e.canonical_name for e in vocab.entities.values()] == ["回焊爐"]
    assert [link.basis for link in vocab.links] == ["identical"]


def test_the_same_pair_is_only_ever_proposed_once():
    """A batch can name the same two identities twice — the proposal queue is read
    by a person, and asking them the same question twice is how a queue stops
    being read."""
    from workspace_app.kb.graph.build import build_vocabulary

    llm = _FakeLlm(
        '{"groups": ['
        '{"names": ["回焊爐", "Reflow Oven"], "why": "the oven"},'
        '{"names": ["Reflow Oven", "回焊爐"], "why": "the oven again"}]}'
    )
    vocab = build_vocabulary(
        [_mention("deck-A", "回焊爐"), _mention("deck-B", "Reflow Oven")], [], llm=llm
    )
    assert len([link for link in vocab.links if link.state == "pending"]) == 1


def test_an_unreadable_grouping_reply_asks_nobody_anything():
    """This path can only ADD work for a person, so a confused answer should
    produce no work at all."""
    from workspace_app.kb.graph.build import build_vocabulary

    evidence = [_mention("deck-A", "回焊爐"), _mention("deck-B", "Reflow Oven")]
    for reply in (
        "I could not group anything.",  # no JSON object at all
        '{"groups": [ }',  # malformed
        '{"groups": ["回焊爐", 42]}',  # entries that are not groups
        '{"groups": [{"names": ["回焊爐"], "why": "alone"}]}',  # a group of one
    ):
        vocab = build_vocabulary(evidence, [], llm=_FakeLlm(reply))
        assert [link for link in vocab.links if link.state == "pending"] == [], reply


def test_evidence_with_no_comparison_key_is_not_an_identity():
    """A surface that normalises to nothing names nothing, and a connecting word
    that does is not a connection anyone can group on. Both would otherwise mint
    an identity keyed on the empty string, which every other empty one would then
    join."""
    from workspace_app.kb.graph.build import RelationEvidence, build_vocabulary

    vocab = build_vocabulary(
        [_mention("deck-A", "回焊爐"), _mention("deck-A", "   ")],
        [
            _relationship("deck-A", "回焊爐", "造成", "冷焊"),
            RelationEvidence(norm_predicate="", predicate="  ", collection_id="c1"),
        ],
    )

    assert sorted(e.canonical_name for e in vocab.entities.values()) == ["回焊爐", "造成"]


def test_a_declared_alias_keeps_its_keys_when_the_host_is_also_a_predicate():
    """The predicate pass runs after the grouping and writes the identity at the
    same id. Writing `norm_keys=[key]` there throws away the keys the declaration
    just merged in, and the absorbed name then lives on NO row: the tombstone
    holds none and the host lost it. Everything that resolves a name — the
    agent's `lookup_entity`, the review queue, an entity's claims — misses it."""
    from workspace_app.kb.graph.build import build_vocabulary
    from workspace_app.resources.graph import entity_id

    quote = "導致(又稱造成)"
    vocab = build_vocabulary(
        [
            _mention("deck-A", "導致", same_as=["造成"], quote=quote),
            _mention("deck-A", "造成", same_as=["導致"], quote=quote),
        ],
        [_relationship("deck-B", "回焊爐", "造成", "冷焊")],
    )

    host = vocab.entities[entity_id("導致")]
    assert host.norm_keys == ["導致", "造成"], "the predicate pass discarded the merged key"
    # and no live identity may hold the absorbed key twice
    live = [e for e in vocab.entities.values() if e.collection_ids]
    assert sum("造成" in e.norm_keys for e in live) == 1


def _proposed_pairs(vocab):
    return {(link.entity_id, link.proposed_from) for link in vocab.links if link.state == "pending"}


def test_a_proposal_names_the_same_pair_however_the_model_words_its_reason():
    """The pair is the question put to a person. If the reason is part of the
    proposal's identity, a model that rewords it next week asks the same question
    again — and the answer already given is filed against a row nobody looks up."""
    from workspace_app.kb.graph.build import build_vocabulary

    evidence = [_mention("deck-A", "回焊爐"), _mention("deck-B", "Reflow Oven")]
    proposals = []
    for why in ("the oven that melts solder paste", "the reflow oven machine"):
        vocab = build_vocabulary(
            evidence,
            [],
            llm=_FakeLlm(f'{{"groups": [{{"names": ["回焊爐", "Reflow Oven"], "why": "{why}"}}]}}'),
        )
        (link,) = [x for x in vocab.links if x.state == "pending"]
        proposals.append(link)

    first, second = proposals
    assert first.evidence != second.evidence, "the reason did not change; this proves nothing"
    # Everything that says WHICH pair is being asked about is identical …
    assert (first.entity_id, first.mention_id, first.basis, first.proposed_from) == (
        second.entity_id,
        second.mention_id,
        second.basis,
        second.proposed_from,
    )


def test_which_side_of_a_proposal_is_the_host_does_not_follow_the_display_name():
    """The display name is whatever surface the corpus used MOST, so one new
    document flips it. If the host follows it, the pair's identity changes under
    a decision already recorded against the old one."""
    from workspace_app.kb.graph.build import build_vocabulary

    reply = '{"groups": [{"names": ["%s", "%s"], "why": "one thing"}]}'
    first = build_vocabulary(
        [_mention("deck-A", "Alpha", occurrences=9), _mention("deck-B", "Beta")],
        [],
        llm=_FakeLlm(reply % ("Alpha", "Beta")),
    )
    # A later deck writes the SAME normalised key ("alpha") under a surface used
    # more often, so the display name changes — and with it the alphabetical
    # order of the two names — while both KEYS, and both ids, are untouched.
    second = build_vocabulary(
        [
            _mention("deck-A", "Alpha", occurrences=9),
            _mention("deck-C", "aLPHA", occurrences=99),
            _mention("deck-B", "Beta"),
        ],
        [],
        llm=_FakeLlm(reply % ("aLPHA", "Beta")),
    )
    assert [e.canonical_name for e in second.entities.values() if e.canonical_name == "aLPHA"], (
        "the display name did not change, so this test is not exercising the flip"
    )
    assert _proposed_pairs(first) == _proposed_pairs(second)


def test_which_surface_is_stored_does_not_depend_on_the_order_chunks_arrive():
    """Nothing orders the chunk read, so "the first surface wins" means the
    stored display form follows whatever order the store happened to return —
    and a re-extraction that changes it makes a diff of two previews show churn
    that is not a change in the extraction at all."""
    llm_a = _FakeLlm(
        '{"mentions": [{"surface": "Reflow Oven"}]}', '{"mentions": [{"surface": "reflow  oven"}]}'
    )
    llm_b = _FakeLlm(
        '{"mentions": [{"surface": "reflow  oven"}]}', '{"mentions": [{"surface": "Reflow Oven"}]}'
    )
    forwards = build_doc_graph(
        llm_a, DocSource(doc_id="d", chunks=[("c1", "x"), ("c2", "y")]), collection_id="c"
    )
    backwards = build_doc_graph(
        llm_b, DocSource(doc_id="d", chunks=[("c1", "x"), ("c2", "y")]), collection_id="c"
    )
    assert [m.surface for m in forwards.mentions] == [m.surface for m in backwards.mentions]


def test_two_surfaces_used_equally_often_settle_deterministically():
    """The tie-break is what the docstring says exists "so a re-run cannot
    shuffle the name". Without it the winner is whichever `max` saw first, which
    is dict order — stable within a process, not across a re-extraction."""
    from workspace_app.kb.graph.build import build_vocabulary

    forwards = build_vocabulary(
        [_mention("deck-A", "Reflow Oven"), _mention("deck-B", "reflow  oven")], []
    )
    backwards = build_vocabulary(
        [_mention("deck-B", "reflow  oven"), _mention("deck-A", "Reflow Oven")], []
    )
    names = {e.canonical_name for e in forwards.entities.values()}
    assert names == {e.canonical_name for e in backwards.entities.values()}
    assert len(names) == 1


def test_a_kind_a_person_merged_is_still_what_its_things_point_at():
    """Two kind labels — 「機台」 and "tool" — are identities with no mentions of
    their own: things are LABELLED with them, nothing names them. So the only way
    they ever merge is a person accepting the proposal, and that merge has to
    reach the kind pass. If it does not, the kind is minted at the absorbed key
    while every thing points at the host, and `kind_id` resolves to nothing at
    all."""
    from workspace_app.kb.graph.build import Decisions, build_vocabulary
    from workspace_app.resources.graph import entity_id

    machine, tool = entity_id("機台"), entity_id("tool")
    vocab = build_vocabulary(
        [
            _mention("deck-A", "回焊爐", kind="機台", collection="c1"),
            _mention("deck-B", "SPI", kind="tool", collection="c2"),
        ],
        [],
        decisions=Decisions(accepted=frozenset({(machine, tool, "alice")})),
    )

    kinds = {e.kind_id for e in vocab.entities.values() if e.kind_id}
    assert len(kinds) == 1, "the two labels did not become one kind"
    (kind_id,) = kinds
    assert kind_id in vocab.entities, "every thing points at a kind that does not exist"
    kind = vocab.entities[kind_id]
    assert kind.collection_ids == ["c1", "c2"], "the merged kind lost the evidence for it"
    assert set(kind.norm_keys) == {"機台", "tool"}
    # The absorbed label keeps a tombstone too. Nothing MENTIONS a kind, so it
    # used to fall outside the loop that writes them — and an id someone still
    # holds then 404s instead of saying where its evidence went.
    absorbed = (entity_id("機台"), entity_id("tool"))
    (gone,) = [eid for eid in absorbed if eid != kind_id]
    assert vocab.entities[gone].merged_into == kind_id


def test_a_graph_says_whether_anyone_actually_asked_for_merge_proposals():
    """#697 P11 — "the model proposed nothing" and "nobody asked the model" are
    the same empty list, and telling them apart is what stops a store from
    emptying a review queue nobody answered.

    ``Vocabulary`` records it and ``persist`` acts on it, but ``build_graph`` was
    dropping it on the way out, so ``Graph.proposed`` was False even when the
    pass had run — a field whose whole reason to exist is that distinction,
    quietly answering "nobody asked" every time. Nothing read it yet, which is
    exactly the kind of trap that is cheap now and expensive the day someone
    wires the preview to a writer.
    """
    from workspace_app.kb.graph.build import build_graph

    def run(*, ask: bool):
        llm = _FakeLlm(
            '{"mentions": [{"surface": "回焊爐", "kind": "機台"},'
            ' {"surface": "Reflow Oven", "kind": "machine"}]}',
            '{"groups": []}',
        )
        return build_graph(
            llm,
            [DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐 / Reflow Oven")])],
            collection_id="col-1",
            propose_with=llm if ask else None,
        )

    asked = run(ask=True)
    assert asked.proposed is True
    # An empty answer from a model that WAS asked still counts as having asked.
    assert [link for link in asked.links if link.state == "pending"] == []
    assert run(ask=False).proposed is False


def test_passages_can_be_extracted_several_at_a_time():
    """One model call per passage, run serially, is the whole cost of a run —
    measured at ~107 seconds per document on the real corpus. The calls do not
    depend on each other, so the only reason they were serial is that nobody
    said otherwise.

    Asserted on CONCURRENCY, not on wall clock: a timing assertion on a machine
    under load is a flaky test that teaches nothing."""
    import threading

    peak = 0
    live = 0
    lock = threading.Lock()

    class _Slow(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            nonlocal peak, live
            with lock:
                live += 1
                peak = max(peak, live)
            try:
                import time as _t

                _t.sleep(0.05)
                yield '{"mentions": [{"surface": "回焊爐"}]}', False
            finally:
                with lock:
                    live -= 1

    build_doc_graph(
        _Slow(),
        DocSource(doc_id="d", chunks=[(f"c{i}", f"t{i}") for i in range(8)]),
        collection_id="c",
        concurrency=4,
    )

    assert peak > 1, "the passages were still extracted one at a time"
    assert peak <= 4, f"more calls were in flight ({peak}) than the limit allowed"


def test_the_passages_keep_their_order_however_they_finish():
    """Concurrency must not reorder them: passage order decides which kind and
    which declared quote a mention keeps, and the order statements are written
    in. A run that reordered would produce a different graph each time from the
    same corpus."""
    replies = [f'{{"mentions": [{{"surface": "w{i}"}}]}}' for i in range(6)]

    class _OutOfOrder(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            import time as _t

            index = int(prompt.rsplit("t", 1)[-1])
            _t.sleep(0.02 * (6 - index))  # later passages answer FIRST
            yield replies[index], False

    graph = build_doc_graph(
        _OutOfOrder(),
        DocSource(doc_id="d", chunks=[(f"c{i}", f"t{i}") for i in range(6)]),
        collection_id="c",
        concurrency=6,
    )

    assert [m.surface for m in graph.mentions] == [f"w{i}" for i in range(6)]


def test_a_connection_whose_ends_are_not_things_is_not_a_connection():
    """The extractor was returning sentence parses rather than graph edges:
    「第一個 —是→ 汽車」 from a passage that said "the first one is a car". 「第一個」
    points at context and means nothing outside the sentence it came from; 「是」
    is a copula, not a relation.

    The rule is structural, not a guess about spelling: an edge joins two THINGS,
    so an end that the same answer did not name as a thing is not an end. It also
    means the connection layer inherits the criterion for free — improve what
    counts as a thing and the edges improve with it, without a second rule to
    keep in step.
    """
    llm = _FakeLlm(
        '{"mentions": [{"surface": "回焊爐", "kind": "機台"}, {"surface": "冷焊", "kind": "缺陷"}],'
        ' "relationships": ['
        '{"subject": "回焊爐", "predicate": "造成", "object": "冷焊"},'
        '{"subject": "第一個", "predicate": "是", "object": "汽車"}]}'
    )

    graph = build_doc_graph(
        llm,
        DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐造成冷焊。第一個是汽車。")]),
        collection_id="col-1",
    )

    assert [(r.subject, r.predicate, r.object) for r in graph.relationships] == [
        ("回焊爐", "造成", "冷焊")
    ]


def test_a_connection_is_kept_when_a_LATER_passage_named_its_ends():
    """The test is the whole DOCUMENT's things, not the passage's. A deck that
    names the machine on slide one and states the connection on slide four is
    the ordinary case, and judging per passage would throw it away."""
    llm = _FakeLlm(
        '{"mentions": [{"surface": "回焊爐"}, {"surface": "冷焊"}]}',
        '{"relationships": [{"subject": "回焊爐", "predicate": "造成", "object": "冷焊"}]}',
    )

    graph = build_doc_graph(
        llm,
        DocSource(doc_id="deck-A", chunks=[("c1", "回焊爐、冷焊"), ("c2", "回焊爐造成冷焊")]),
        collection_id="col-1",
    )

    assert len(graph.relationships) == 1


def test_a_decision_about_evidence_that_is_gone_is_dropped():
    """A person merged two things and one has since left the corpus — the deck
    was deleted, or a new criterion no longer names it. There is nothing left to
    merge, and re-inventing the key would put a name in the vocabulary that no
    document says."""
    from workspace_app.kb.graph.build import Decisions, build_vocabulary
    from workspace_app.resources.graph import entity_id

    vocab = build_vocabulary(
        [_mention("deck-A", "回焊爐")],
        [],
        decisions=Decisions(accepted=frozenset({(entity_id("回焊爐"), entity_id("gone"), "amy")})),
    )

    live = [e for e in vocab.entities.values() if e.collection_ids]
    assert [e.canonical_name for e in live] == ["回焊爐"]
    assert entity_id("gone") not in vocab.entities


def test_a_pair_both_accepted_and_rejected_is_not_merged():
    """Two answers that contradict each other, and nothing here can tell which
    came last. It takes the side this module always takes when it cannot be sure:
    it may fail to merge, it may never merge wrongly — a failure to merge is two
    entries someone can see, a wrong merge is one entry that quietly lost a
    distinction."""
    from workspace_app.kb.graph.build import Decisions, build_vocabulary
    from workspace_app.resources.graph import entity_id

    pair = (entity_id("回焊爐"), entity_id("reflow oven"))
    evidence = [_mention("deck-A", "回焊爐"), _mention("deck-B", "Reflow Oven")]
    for rejected in (pair, (pair[1], pair[0])):  # recorded either way round
        vocab = build_vocabulary(
            evidence,
            [],
            decisions=Decisions(
                accepted=frozenset({(*pair, "amy")}), rejected=frozenset({rejected})
            ),
        )
        live = [e for e in vocab.entities.values() if e.collection_ids]
        assert len(live) == 2, f"a contradicted decision was applied anyway ({rejected})"
