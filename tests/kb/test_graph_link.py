"""#534 B — building the vocabulary from evidence, deterministically.

This is the first and safest of the four bases: mentions whose comparison key is
identical are one thing. It needs no model and no reviewer, and it is the bulk of
the work — most of what makes two surfaces differ is typing noise the key already
removed.

The job is a RECONCILE, not a one-shot build: it re-runs, and running it twice
must change nothing the second time. Entities and their links accumulate; nothing
is rebuilt from scratch, because the links are what a human's decisions are
recorded as and a rebuild would throw them away.
"""

from __future__ import annotations

from specstar import QB, SpecStar

from workspace_app.kb.graph.link import differs_by_number, link_identical_mentions
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphEntity, GraphEntityLink, GraphMention, mention_id
from workspace_app.resources.kb import Collection


def _collection(spec: SpecStar, name: str = "c") -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using("bob"):
        return rm.create(Collection(name=name)).resource_id


def _mention(spec: SpecStar, cid: str, doc: str, surface: str, *, kind: str = "", n: int = 1):
    rm = spec.get_resource_manager(GraphMention)
    with rm.using("bob"):
        rm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id=doc,
                surface=surface,
                norm_surface=norm_surface(surface),
                kind=kind,
                norm_kind=norm_surface(kind),
                occurrences=n,
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id(doc, surface),
        )


def _entities(spec: SpecStar) -> list[GraphEntity]:
    rm = spec.get_resource_manager(GraphEntity)
    out = []
    for r in rm.list_resources(QB.all().build()):
        assert isinstance(r.data, GraphEntity)
        out.append(r.data)
    return out


def _links(spec: SpecStar) -> list[GraphEntityLink]:
    rm = spec.get_resource_manager(GraphEntityLink)
    out = []
    for r in rm.list_resources(QB.all().build()):
        assert isinstance(r.data, GraphEntityLink)
        out.append(r.data)
    return out


class TestDiffersByNumber:
    """The one hard veto. Two surfaces whose digits differ are different things —
    RO-3 and RO-4, part A1203 and A1204 — and they are exactly the pairs a
    similarity score cannot tell apart, since they differ by one character out of
    many. Vetoed outright rather than queued: asking a person to reject them one
    by one is spending the scarcest thing here on a question a rule can answer."""

    def test_a_differing_digit_is_a_veto(self):
        assert differs_by_number("RO-3", "RO-4")
        assert differs_by_number("A1203", "A1204")

    def test_matching_digits_are_not(self):
        assert not differs_by_number("Reflow Oven", "reflow oven")
        assert not differs_by_number("RO-3", "RO-3 爐")

    def test_no_digits_at_all_is_not(self):
        assert not differs_by_number("回焊爐", "Reflow Oven")

    def test_leading_zeros_do_not_make_a_difference(self):
        """RO-03 and RO-3 are the same number written two ways. The veto is about
        the VALUE, not the spelling — deciding whether the two are the same machine
        is a later question, and this rule must not pre-empt it by vetoing."""
        assert not differs_by_number("RO-03", "RO-3")


def test_mentions_with_one_key_become_one_entity():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "Reflow Oven", n=3)
    _mention(spec, cid, "deck-B", "  reflow   oven ", n=1)
    link_identical_mentions(spec)

    (entity,) = _entities(spec)
    assert entity.norm_keys == [norm_surface("reflow oven")]
    assert len(_links(spec)) == 2
    assert {link.basis for link in _links(spec)} == {"identical"}
    assert {link.state for link in _links(spec)} == {"active"}


def test_the_display_name_is_the_surface_the_documents_used_most():
    """A name someone actually wrote, not a normalised string nobody did. Ties
    break on the surface itself so a re-run does not shuffle the name."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "reflow oven", n=1)
    _mention(spec, cid, "deck-B", "Reflow Oven", n=5)
    link_identical_mentions(spec)
    (entity,) = _entities(spec)
    assert entity.canonical_name == "Reflow Oven"


def test_different_keys_stay_different_entities():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-A", "錫膏")
    link_identical_mentions(spec)
    assert len(_entities(spec)) == 2


def test_running_twice_changes_nothing():
    """The reconcile re-runs on a schedule. A second pass that duplicated entities
    or links would compound every week, and the links are where human decisions
    live — they are accumulated, never rebuilt."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    link_identical_mentions(spec)
    link_identical_mentions(spec)
    assert len(_entities(spec)) == 1
    assert len(_links(spec)) == 1


def test_an_entity_records_every_collection_its_evidence_came_from():
    """That list is what the access scope reads, so it has to grow as evidence
    arrives — an entity whose list lags is invisible to people who should see it."""
    spec = make_spec(default_user=lambda: "bob")
    one, two = _collection(spec, "one"), _collection(spec, "two")
    _mention(spec, one, "deck-A", "回焊爐")
    link_identical_mentions(spec)
    _mention(spec, two, "deck-B", "回焊爐")
    link_identical_mentions(spec)
    (entity,) = _entities(spec)
    assert sorted(entity.collection_ids) == sorted([one, two])


def test_a_new_document_joins_the_entity_that_already_exists():
    """Identity is stable across runs: later evidence attaches to the identity that
    is already there rather than starting a second one beside it."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    link_identical_mentions(spec)
    first = _entities(spec)[0]
    _mention(spec, cid, "deck-B", "回焊爐")
    link_identical_mentions(spec)
    assert len(_entities(spec)) == 1
    assert len(_links(spec)) == 2
    assert _entities(spec)[0].canonical_name == first.canonical_name


def test_a_kind_becomes_an_entity_too():
    """ "機台" is an identity like any other, so the same pass creates it and points
    the thing at it — one mechanism, so the taxonomy comes out of the data."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐", kind="機台")
    link_identical_mentions(spec)
    by_name = {e.canonical_name: e for e in _entities(spec)}
    assert set(by_name) == {"回焊爐", "機台"}
    assert by_name["回焊爐"].kind_id
    assert by_name["機台"].kind_id == ""  # the recursion stops at a kind


class TestDeclaredAliases:
    """The strongest basis short of a human: the document declaring its own
    equivalence. It needs no model — the declaration is written INSIDE the surface
    ("回焊爐(Reflow Oven)"), which is exactly why the entity key keeps
    parentheticals that the metric key strips."""

    def test_a_parenthetical_name_is_a_declared_alias(self):
        from workspace_app.kb.graph.link import declared_aliases

        assert declared_aliases("回焊爐(Reflow Oven)") == [("回焊爐", "Reflow Oven")]
        assert declared_aliases("Reflow Oven (RO-3)") == [("Reflow Oven", "RO-3")]

    def test_a_parenthetical_measurement_is_not_an_alias(self):
        """ "回焊爐(250°C)" states a setting, not another name for the oven. A
        bracket only declares an alias when what is inside could BE a name."""
        from workspace_app.kb.graph.link import declared_aliases

        assert declared_aliases("回焊爐(250°C)") == []
        assert declared_aliases("Yield (%)") == []
        assert declared_aliases("產能(2024)") == []

    def test_a_declaration_whose_numbers_disagree_is_refused(self):
        """No document means "RO-3 is another name for RO-4"; that is a typo or a
        list. The number veto applies to a declaration too."""
        from workspace_app.kb.graph.link import declared_aliases

        assert declared_aliases("RO-3(RO-4)") == []

    def test_a_name_and_a_code_are_still_an_alias(self):
        """ "回焊爐(RO-3)" is the commonest form of all: the general name beside the
        specific code. One side simply has no number to disagree with, so the veto
        must not fire — it exists to stop two DIFFERENT numbers merging."""
        from workspace_app.kb.graph.link import declared_aliases

        assert declared_aliases("回焊爐(RO-3)") == [("回焊爐", "RO-3")]


def test_a_declared_alias_joins_two_entities():
    """The payoff: one document writing "回焊爐(Reflow Oven)" is enough for every
    other document's "Reflow Oven" to resolve to the same identity — including the
    ones that only ever use the English. This is what makes an entity page whole
    across languages, and no model was asked."""
    from workspace_app.kb.graph.link import link_declared_aliases

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐(Reflow Oven)")
    _mention(spec, cid, "deck-B", "Reflow Oven")
    _mention(spec, cid, "deck-C", "回焊爐")
    link_identical_mentions(spec)
    assert len(_entities(spec)) == 3  # three distinct keys before the declaration

    link_declared_aliases(spec)
    keyed = {k: e for e in _entities(spec) for k in e.norm_keys}
    assert keyed[norm_surface("回焊爐")] is keyed[norm_surface("Reflow Oven")] or (
        keyed[norm_surface("回焊爐")].canonical_name
        == keyed[norm_surface("Reflow Oven")].canonical_name
    )
    declared = [link for link in _links(spec) if link.basis == "declared"]
    assert declared and declared[0].evidence == "回焊爐(Reflow Oven)"


def test_linking_declared_aliases_twice_changes_nothing():
    from workspace_app.kb.graph.link import link_declared_aliases

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐(Reflow Oven)")
    _mention(spec, cid, "deck-B", "Reflow Oven")
    link_identical_mentions(spec)
    link_declared_aliases(spec)
    before = len(_entities(spec)), len(_links(spec))
    link_declared_aliases(spec)
    assert (len(_entities(spec)), len(_links(spec))) == before
