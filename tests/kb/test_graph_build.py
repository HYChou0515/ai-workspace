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
        '{"mentions": [{"surface": "回焊爐", "kind": "機台"}],'
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
    # both decks' spellings landed on ONE identity, and 機台 / 造成 are identities too
    names = {e.canonical_name for e in graph.entities.values()}
    assert names == {"回焊爐", "機台", "造成"}
    assert len([link for link in graph.links if link.basis == "identical"]) == 2


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
