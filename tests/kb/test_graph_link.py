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

from collections.abc import Iterator

from specstar import QB, SpecStar

from workspace_app.kb.graph.link import reconcile_vocabulary
from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.kb.llm import ILlm
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
    reconcile_vocabulary(spec, llm=None)

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
    reconcile_vocabulary(spec, llm=None)
    (entity,) = _entities(spec)
    assert entity.canonical_name == "Reflow Oven"


def test_different_keys_stay_different_entities():
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-A", "錫膏")
    reconcile_vocabulary(spec, llm=None)
    assert len(_entities(spec)) == 2


def test_running_twice_changes_nothing():
    """The reconcile re-runs on a schedule. A second pass that duplicated entities
    or links would compound every week, and the links are where human decisions
    live — they are accumulated, never rebuilt."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    reconcile_vocabulary(spec, llm=None)
    reconcile_vocabulary(spec, llm=None)
    assert len(_entities(spec)) == 1
    assert len(_links(spec)) == 1


def test_an_entity_records_every_collection_its_evidence_came_from():
    """That list is what the access scope reads, so it has to grow as evidence
    arrives — an entity whose list lags is invisible to people who should see it."""
    spec = make_spec(default_user=lambda: "bob")
    one, two = _collection(spec, "one"), _collection(spec, "two")
    _mention(spec, one, "deck-A", "回焊爐")
    reconcile_vocabulary(spec, llm=None)
    _mention(spec, two, "deck-B", "回焊爐")
    reconcile_vocabulary(spec, llm=None)
    (entity,) = _entities(spec)
    assert sorted(entity.collection_ids) == sorted([one, two])


def test_a_new_document_joins_the_entity_that_already_exists():
    """Identity is stable across runs: later evidence attaches to the identity that
    is already there rather than starting a second one beside it."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    reconcile_vocabulary(spec, llm=None)
    first = _entities(spec)[0]
    _mention(spec, cid, "deck-B", "回焊爐")
    reconcile_vocabulary(spec, llm=None)
    assert len(_entities(spec)) == 1
    assert len(_links(spec)) == 2
    assert _entities(spec)[0].canonical_name == first.canonical_name


def test_a_kind_becomes_an_entity_too():
    """ "機台" is an identity like any other, so the same pass creates it and points
    the thing at it — one mechanism, so the taxonomy comes out of the data."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐", kind="機台")
    reconcile_vocabulary(spec, llm=None)
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
    from workspace_app.kb.graph.link import reconcile_vocabulary

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _declaring_mention(spec, cid, "deck-A", "回焊爐", "RO", "回焊爐,以下簡稱 RO")
    _mention(spec, cid, "deck-B", "RO")
    reconcile_vocabulary(spec, llm=None)
    assert len(_entities(spec)) == 2

    reconcile_vocabulary(spec, llm=None)
    live = [e for e in _entities(spec) if e.collection_ids]
    assert len(live) == 1
    assert sorted(live[0].norm_keys) == sorted([norm_surface("回焊爐"), norm_surface("RO")])
    declared = [link for link in _links(spec) if link.basis == "declared"]
    assert declared and declared[0].evidence == "deck-A: 回焊爐,以下簡稱 RO"


def test_applying_declarations_twice_changes_nothing():
    from workspace_app.kb.graph.link import reconcile_vocabulary

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _declaring_mention(spec, cid, "deck-A", "回焊爐", "RO", "回焊爐,以下簡稱 RO")
    _mention(spec, cid, "deck-B", "RO")
    reconcile_vocabulary(spec, llm=None)
    reconcile_vocabulary(spec, llm=None)
    before = len(_entities(spec)), len(_links(spec))
    reconcile_vocabulary(spec, llm=None)
    assert (len(_entities(spec)), len(_links(spec))) == before


def test_an_absorbed_identity_says_where_it_went():
    """It keeps no keys and no evidence, so nobody can reach it — but the row
    stays, because a merge has to be undoable and a row that cannot say where it
    went is a dead end. An unexplained empty identity also reads as corruption to
    whoever finds it next."""
    from workspace_app.kb.graph.link import reconcile_vocabulary

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _declaring_mention(spec, cid, "deck-A", "回焊爐", "RO", "回焊爐,以下簡稱 RO")
    _mention(spec, cid, "deck-B", "RO")
    reconcile_vocabulary(spec, llm=None)
    reconcile_vocabulary(spec, llm=None)

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

    def _boom(_spec: SpecStar) -> list:
        raise _PgError("stack depth limit exceeded")

    monkeypatch.setattr(link_mod, "read_relation_evidence", _boom)
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
    assert "read_evidence" in message, (
        f"the log does not say WHICH stage died, which is the whole point: {message!r}"
    )
    assert record.exc_info is not None, "the traceback was not captured"
    assert "IN (('r1'" in message, f"the failing statement never reached the log: {message!r}"


def test_evidence_that_is_gone_takes_its_identity_with_it():
    """The vocabulary is a reconcile to what the evidence now supports, not an
    accumulation. A deck deleted (or re-extracted under a criterion that no longer
    names something) must not leave an identity standing that nothing vouches for
    — a name alone can leak, and a page holding no evidence is a page nobody can
    check."""
    from workspace_app.kb.graph.persist import wipe_doc_graph

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "錫膏")
    reconcile_vocabulary(spec, llm=None)
    assert len(_entities(spec)) == 2

    wipe_doc_graph(spec, "deck-B")
    reconcile_vocabulary(spec, llm=None)

    assert [e.canonical_name for e in _entities(spec)] == ["回焊爐"]
    lrm = spec.get_resource_manager(GraphEntityLink)
    assert len(list(lrm.list_resources(QB.all().build()))) == 1


def test_a_failure_carrying_no_statement_still_names_its_stage(caplog, monkeypatch):
    """Most exceptions are not database errors and carry no SQL. The log has to
    stay useful for those too — the stage name and the traceback are the whole
    point, and the statement is a bonus when there is one."""
    import logging

    import pytest

    from workspace_app.kb.graph import link as link_mod

    def _boom(_spec: SpecStar) -> list:
        raise RuntimeError("something ordinary broke")

    monkeypatch.setattr(link_mod, "read_relation_evidence", _boom)
    spec = make_spec(default_user=lambda: "bob")

    with (
        caplog.at_level(logging.INFO, logger="workspace_app.kb.graph.link"),
        pytest.raises(RuntimeError),
    ):
        reconcile_vocabulary(spec)

    (failed,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "read_evidence" in failed.getMessage()
    assert "no statement on the error" in failed.getMessage()
    assert failed.exc_info is not None


# ── a person's answer has to outlive the recompute ───────────────────
#
# The vocabulary is recomputed from the evidence every week. A decision a PERSON
# made is not in the evidence, so unless it is fed back IN, the recompute either
# throws it away or — worse — half-applies it: the merge comes apart into a live
# duplicate holding no evidence, the host loses the keys the merge gave it, and
# the pair can never be raised again because the settled row still occupies its
# address. All three were reproduced before these tests existed.


class _Judge(ILlm):
    """A model that groups whatever it is told to, with a given reason."""

    def __init__(self, why: str = "one thing") -> None:
        self._why = why

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (
            f'{{"groups": [{{"names": ["回焊爐", "Reflow Oven"], "why": "{self._why}"}}]}}',
            False,
        )


def _pair(spec: SpecStar):
    cid = _collection(spec)
    _mention(spec, cid, "deck-A", "回焊爐")
    _mention(spec, cid, "deck-B", "Reflow Oven")
    return cid


def test_a_merge_a_person_accepted_survives_the_next_reconcile():
    from workspace_app.kb.graph.review import accept_proposal, list_proposals

    spec = make_spec(default_user=lambda: "bob")
    _pair(spec)
    reconcile_vocabulary(spec, llm=_Judge())
    (proposal,) = list_proposals(spec)
    accept_proposal(spec, proposal.entity_id, proposal.proposed_from, by="alice")

    reconcile_vocabulary(spec, llm=_Judge())

    live = [e for e in _entities(spec) if e.collection_ids]
    assert len(live) == 1, "the accepted merge came apart into two identities again"
    (host,) = live
    assert set(host.norm_keys) == {"回焊爐", "reflow oven"}, (
        "the host lost the key the merge gave it, so that name resolves nowhere"
    )
    # …and nothing was resurrected as a live, empty duplicate
    assert all(e.norm_keys == [] for e in _entities(spec) if e.merged_into)


def test_a_rejected_pair_stays_rejected_when_the_model_rewords_its_reason():
    from workspace_app.kb.graph.review import list_proposals, reject_proposal

    spec = make_spec(default_user=lambda: "bob")
    _pair(spec)
    reconcile_vocabulary(spec, llm=_Judge("the oven that melts solder paste"))
    (proposal,) = list_proposals(spec)
    reject_proposal(spec, proposal.entity_id, proposal.proposed_from, by="alice")

    reconcile_vocabulary(spec, llm=_Judge("the reflow oven machine"))

    assert list_proposals(spec) == [], "the pair was put to a person again"
    decided = [link for link in _links(spec) if link.proposed_from]
    assert [link.state for link in decided] == ["rejected"], "the answer itself was deleted"
    assert [link.evidence for link in decided] == ["alice"]


def test_an_accepted_merge_keeps_every_mention_it_was_supposed_to_unify():
    """The point of accepting is that both documents' evidence lands on one
    identity. Verified on the PAGE a reader actually gets, not on the entity row:
    the row can look merged while the links still point at the tombstone, and
    then the merged identity quietly holds half its evidence."""
    from workspace_app.kb.graph.review import accept_proposal, entity_page, list_proposals

    spec = make_spec(default_user=lambda: "bob")
    _pair(spec)
    reconcile_vocabulary(spec, llm=_Judge())
    (proposal,) = list_proposals(spec)
    accept_proposal(spec, proposal.entity_id, proposal.proposed_from, by="alice")
    reconcile_vocabulary(spec, llm=_Judge())

    (host,) = [e for e in _entities(spec) if e.collection_ids]
    host_ids: list[str] = []
    for r in spec.get_resource_manager(GraphEntity).list_resources(QB.all().build()):
        entity = r.data
        assert isinstance(entity, GraphEntity)
        if entity.collection_ids:
            host_ids.append(r.info.resource_id)  # ty: ignore[unresolved-attribute]
    (host_id,) = host_ids
    page = entity_page(spec, host_id, as_user="bob")
    assert sorted(m.surface for m in page.mentions) == ["Reflow Oven", "回焊爐"], (
        "the merged identity lost the evidence the merge was for"
    )
    assert host.canonical_name in {"Reflow Oven", "回焊爐"}


def test_accepting_one_proposal_leaves_another_pending_one_alone():
    """A run can propose two merges that share a side. Answering one must not
    rewrite the other's question — nor overwrite the reason it was asked, which
    is the only thing telling the reviewer what they are being asked about."""
    from workspace_app.kb.graph.review import accept_proposal, list_proposals

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    for doc, surface in (("d0", "w0"), ("d1", "w1"), ("d2", "w2")):
        _mention(spec, cid, doc, surface)

    class _TwoGroups(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield (
                '{"groups": ['
                '{"names": ["w0", "w1"], "why": "the first pair"},'
                '{"names": ["w0", "w2"], "why": "the second pair"}]}',
                False,
            )

    reconcile_vocabulary(spec, llm=_TwoGroups())
    before = {(p.entity_id, p.proposed_from, p.why) for p in list_proposals(spec)}
    assert len(before) == 2

    target = next(iter(before))
    accept_proposal(spec, target[0], target[1], by="carol")

    survivors = {(p.entity_id, p.proposed_from, p.why) for p in list_proposals(spec)}
    assert survivors == before - {target}, "answering one question rewrote another"


def test_a_rejection_still_holds_once_the_other_side_gains_new_evidence():
    """The stable address alone suppresses a re-proposal only while nothing about
    the pair changes. A new document naming one side changes its display name and
    its evidence — and then only the recorded ANSWER keeps the question from
    being asked again."""
    from workspace_app.kb.graph.review import list_proposals, reject_proposal

    spec = make_spec(default_user=lambda: "bob")
    cid = _pair(spec)
    reconcile_vocabulary(spec, llm=_Judge())
    (proposal,) = list_proposals(spec)
    reject_proposal(spec, proposal.entity_id, proposal.proposed_from, by="alice")

    # a later deck writes the same key far more often, changing what the model is
    # shown and what the identity is called
    _mention(spec, cid, "deck-C", "REFLOW OVEN", n=99)
    reconcile_vocabulary(spec, llm=_Judge())

    assert list_proposals(spec) == [], "the answer stopped holding once evidence moved"
