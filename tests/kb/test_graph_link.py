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

from workspace_app.kb.graph.link import link_identical_mentions
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


def _declaring_mention(spec, cid: str, doc: str, surface: str, same_as: str, quote: str):
    from workspace_app.resources.graph import GraphMention as _M

    rm = spec.get_resource_manager(_M)
    with rm.using("bob"):
        rm.create(
            _M(
                collection_id=cid,
                source_doc_id=doc,
                surface=surface,
                norm_surface=norm_surface(surface),
                declared_same_as=[norm_surface(same_as)],
                declared_quote=quote,
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id(doc, surface),
        )


def test_a_declaration_joins_two_identities_without_a_reviewer():
    """The payoff, and the reason a model-reported declaration still applies on its
    own: one document stating the equivalence resolves every other document's
    "RO" — including the ones that only ever use it — to the same identity. The
    link records the sentence, so anyone can check what it rested on."""
    from workspace_app.kb.graph.link import link_declared_aliases

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _declaring_mention(spec, cid, "deck-A", "回焊爐", "RO", "回焊爐,以下簡稱 RO")
    _mention(spec, cid, "deck-B", "RO")
    link_identical_mentions(spec)
    assert len(_entities(spec)) == 2

    assert link_declared_aliases(spec) == 1
    live = [e for e in _entities(spec) if e.collection_ids]
    assert len(live) == 1
    assert sorted(live[0].norm_keys) == sorted([norm_surface("回焊爐"), norm_surface("RO")])
    declared = [link for link in _links(spec) if link.basis == "declared"]
    assert declared and declared[0].evidence == "deck-A: 回焊爐,以下簡稱 RO"


def test_applying_declarations_twice_changes_nothing():
    from workspace_app.kb.graph.link import link_declared_aliases

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _declaring_mention(spec, cid, "deck-A", "回焊爐", "RO", "回焊爐,以下簡稱 RO")
    _mention(spec, cid, "deck-B", "RO")
    link_identical_mentions(spec)
    link_declared_aliases(spec)
    before = len(_entities(spec)), len(_links(spec))
    assert link_declared_aliases(spec) == 0
    assert (len(_entities(spec)), len(_links(spec))) == before


def test_an_absorbed_identity_says_where_it_went():
    """It keeps no keys and no evidence, so nobody can reach it — but the row
    stays, because a merge has to be undoable and a row that cannot say where it
    went is a dead end. An unexplained empty identity also reads as corruption to
    whoever finds it next."""
    from workspace_app.kb.graph.link import link_declared_aliases

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _declaring_mention(spec, cid, "deck-A", "回焊爐", "RO", "回焊爐,以下簡稱 RO")
    _mention(spec, cid, "deck-B", "RO")
    link_identical_mentions(spec)
    link_declared_aliases(spec)

    host = [e for e in _entities(spec) if e.collection_ids]
    ghost = [e for e in _entities(spec) if not e.collection_ids]
    assert len(host) == 1 and len(ghost) == 1
    assert ghost[0].merged_into
    assert ghost[0].norm_keys == []
