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

from workspace_app.kb.graph.link import link_identical_mentions, reconcile_vocabulary
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


def test_the_vocabulary_pass_reports_what_it_did(caplog):
    """A stage that never ran has to look different from one that ran and found
    nothing.

    Every basis already returns a count and every one of them was discarded, and
    `link.py` logged nothing at all — so in production the vocabulary pass was
    indistinguishable whether it had executed, executed and produced nothing, or
    never been queued. The only way to tell was to open the database and find
    the entity tables empty, which is how a corpus can extract for weeks with no
    entity page and nothing anywhere saying so.
    """
    import logging

    spec = make_spec(default_user=lambda: "bob")
    with caplog.at_level(logging.INFO, logger="workspace_app.kb.graph.link"):
        reconcile_vocabulary(spec)

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "reconcile" in text.lower(), f"the vocabulary pass logged nothing: {text!r}"
    assert "entit" in text.lower() or "link" in text.lower(), (
        f"the log says the pass ran but not what it produced: {text!r}"
    )


def test_a_failed_pass_names_itself_and_keeps_its_traceback(caplog, monkeypatch):
    """A pass that DIES has to say which one it was, and leave a stack behind.

    The queue that runs this keeps only ``str(e)`` (specstar
    ``message_queue/simple.py``), so whatever reconcile does not log itself is
    gone: an operator saw "starting the vocabulary pass over the whole corpus"
    and then one bare sentence, with no way to tell which of the four passes had
    produced it.

    The double here models the real failure — a database error raised from deep
    inside a pass, carrying the offending statement on its cursor the way
    psycopg2 does. For a planner-level error the statement IS the diagnosis, and
    nothing downstream keeps it.
    """
    import logging

    import pytest

    from workspace_app.kb.graph import link as link_mod

    class _Cursor:
        query = b"SELECT ... WHERE (a, b, c) IN (('r1','v1','s'),('r2','v2','s'))"

    class _PgError(Exception):
        cursor = _Cursor()

    def _boom(_spec: SpecStar) -> int:
        raise _PgError("stack depth limit exceeded")

    monkeypatch.setattr(link_mod, "name_predicates", _boom)
    spec = make_spec(default_user=lambda: "bob")

    with (
        caplog.at_level(logging.INFO, logger="workspace_app.kb.graph.link"),
        pytest.raises(_PgError),
    ):
        reconcile_vocabulary(spec)

    failed = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert failed, "a pass died and the log said nothing about it"
    record = failed[0]
    message = record.getMessage()
    assert "name_predicates" in message, (
        f"the log does not say WHICH pass died, which is the whole point: {message!r}"
    )
    assert record.exc_info is not None, "the traceback was not captured"
    assert "IN (('r1'" in message, f"the failing statement never reached the log: {message!r}"
