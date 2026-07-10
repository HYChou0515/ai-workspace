"""The global 審核 (review) inbox aggregation (#481).

``build_review_inbox`` gathers every pending-review item — card-gen proposals +
clarification questions — across every collection the acting user may read,
flattened one row per card / question, each tagged with whether the user can act
on it (write permission). Permission is inherited entirely from the parent
collection, so a private/since-tightened collection's items never leak.
"""

from __future__ import annotations

import msgspec

from workspace_app.kb.card_gen import CardDraft, TermQuestionDraft
from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.review_inbox import build_review_inbox
from workspace_app.perm import Actor
from workspace_app.perm.model import Permission
from workspace_app.resources import Collection, SourceDoc, make_spec


def _collection(spec, name: str, *, owner: str = "u", permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(user=owner):
        return rm.create(Collection(name=name, permission=permission)).resource_id


def _add_source(spec, cid: str, path: str, text: str, *, owner: str = "u") -> str:
    from specstar.types import Binary as _B

    rm = spec.get_resource_manager(SourceDoc)
    with rm.using(user=owner):
        return rm.create(
            SourceDoc(
                collection_id=cid,
                path=path,
                content=_B(data=text.encode()),
                text=text,
                status="ready",
            ),
            resource_id=encode_doc_id(cid, path),
        ).resource_id


class _FakeDrafter:
    def __init__(self, cards, term_qs=None):
        self._cards = cards
        self._term_qs = term_qs or {}

    def digest(self, *, doc_path: str, doc_text: str, collection_id: str = ""):
        from workspace_app.kb.card_gen import DocDigest

        return DocDigest(
            cards=self._cards.get(doc_path, []),
            term_questions=self._term_qs.get(doc_path, []),
        )


def _coord(spec, by_path, *, term_qs=None, owner="u") -> CardGenCoordinator:
    """One coordinator per spec (a second registration of the job model raises)."""
    return CardGenCoordinator(spec, _FakeDrafter(by_path, term_qs), get_user_id=lambda: owner)


async def _run_one(coord, spec, cid, path="a.md", *, owner="u") -> None:
    doc = _add_source(spec, cid, path, "RZ3 is the third reflow zone", owner=owner)
    coord.enqueue(cid, [doc], requested_by=owner)
    await coord.aclose()


async def _seed_run(spec, cid, *, owner="u", cards=None, term_qs=None) -> CardGenCoordinator:
    coord = _coord(spec, cards or {}, term_qs=term_qs, owner=owner)
    await _run_one(coord, spec, cid, owner=owner)
    return coord


async def test_inbox_aggregates_a_collections_pending_card_and_open_question():
    """Tracer: one collection's finalized card proposal + the clarification question
    it raised both surface as inbox rows, tagged with the collection name and — for
    the owner — actionable."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    await _seed_run(
        spec,
        cid,
        cards={"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]},
        term_qs={"a.md": [TermQuestionDraft(term="R7", question="What is R7?")]},
    )
    inbox = build_review_inbox(spec, actor=Actor.human("u"))

    assert [i.card.keys for i in inbox.cards] == [["RZ3"]]
    assert inbox.cards[0].collection_name == "Alpha"
    assert inbox.cards[0].can_act is True  # owner may write
    assert inbox.cards[0].card.id  # addressable
    assert [i.question.term for i in inbox.questions] == ["R7"]
    assert inbox.questions[0].can_act is True


async def test_inbox_hides_items_in_collections_the_user_cannot_read():
    """#481 permission: a private collection's pending items never surface to a user
    who can't read it — visibility is inherited from the collection."""
    spec = make_spec(default_user="owner")
    cid = _collection(spec, "Secret", owner="owner", permission=Permission(visibility="private"))
    await _seed_run(
        spec,
        cid,
        owner="owner",
        cards={"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]},
        term_qs={"a.md": [TermQuestionDraft(term="R7", question="?")]},
    )
    inbox = build_review_inbox(spec, actor=Actor.human("outsider"))
    assert inbox.cards == []
    assert inbox.questions == []


async def test_inbox_shows_readonly_items_when_the_user_lacks_write():
    """#481: an item the user can VIEW but not EDIT still shows, flagged ``can_act
    is False`` so the FE can render it read-only with actions disabled."""
    spec = make_spec(default_user="owner")
    cid = _collection(
        spec,
        "Shared",
        owner="owner",
        permission=Permission(visibility="restricted", read_content=["user:reader"]),
    )
    await _seed_run(
        spec,
        cid,
        owner="owner",
        cards={"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]},
    )
    inbox = build_review_inbox(spec, actor=Actor.human("reader"))
    assert len(inbox.cards) == 1
    assert inbox.cards[0].can_act is False  # read_content granted, add_content not


async def test_inbox_history_shows_resolved_and_default_hides_them():
    """#481: the default inbox is pending-only; ``resolved=True`` is the history
    view — a committed card leaves the pending list and appears in history."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    coord = await _seed_run(
        spec, cid, cards={"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]}
    )
    (item,) = build_review_inbox(spec, actor=Actor.human("u")).cards
    coord.decide(item.run_id, item.card.id, "accepted")
    coord.commit_cards([(item.run_id, item.card.id)])

    assert build_review_inbox(spec, actor=Actor.human("u")).cards == []  # committed → gone
    history = build_review_inbox(spec, actor=Actor.human("u"), resolved=True)
    assert [i.card.decision for i in history.cards] == ["committed"]


async def test_inbox_can_scope_to_one_collection():
    """#481: the per-collection 待審核 tab reuses the inbox, scoped to its
    collection via ``collection_id``."""
    spec = make_spec(default_user="u")
    c1 = _collection(spec, "One")
    c2 = _collection(spec, "Two")
    coord = _coord(
        spec,
        {
            "one.md": [CardDraft(keys=["A"], title="A", snippet="s")],
            "two.md": [CardDraft(keys=["B"], title="B", snippet="s")],
        },
    )
    d1 = _add_source(spec, c1, "one.md", "x")
    d2 = _add_source(spec, c2, "two.md", "y")
    coord.enqueue(c1, [d1])
    coord.enqueue(c2, [d2])
    await coord.aclose()

    inbox = build_review_inbox(spec, actor=Actor.human("u"), collection_id=c1)
    assert {i.collection_id for i in inbox.cards} == {c1}
    assert [i.card.keys for i in inbox.cards] == [["A"]]


async def test_inbox_limit_caps_the_page_and_reports_total():
    """P1 pagination tracer: ``limit`` caps how many items a single page returns,
    while ``total`` still reports the full filtered count so the FE can render
    "showing X of N"."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    await _seed_run(
        spec,
        cid,
        cards={
            "a.md": [
                CardDraft(keys=["RZ3"], title="RZ3", snippet="s"),
                CardDraft(keys=["RZ4"], title="RZ4", snippet="s"),
                CardDraft(keys=["RZ5"], title="RZ5", snippet="s"),
            ]
        },
    )
    inbox = build_review_inbox(spec, actor=Actor.human("u"), limit=2)
    assert len(inbox.cards) + len(inbox.questions) == 2  # a single page, capped
    assert inbox.total == 3  # the full count, independent of the page size


async def test_offset_pages_through_the_stream_without_overlap_or_gaps():
    """P1 pagination: successive ``offset`` windows partition the full stream —
    every item appears on exactly one page, none twice."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    await _seed_run(
        spec,
        cid,
        cards={
            "a.md": [
                CardDraft(keys=["RZ3"], title="RZ3", snippet="s"),
                CardDraft(keys=["RZ4"], title="RZ4", snippet="s"),
                CardDraft(keys=["RZ5"], title="RZ5", snippet="s"),
            ]
        },
    )

    def keys(inbox) -> set[str]:
        return {c.card.keys[0] for c in inbox.cards}

    p1 = build_review_inbox(spec, actor=Actor.human("u"), limit=2, offset=0)
    p2 = build_review_inbox(spec, actor=Actor.human("u"), limit=2, offset=2)
    assert len(p1.cards) == 2 and len(p2.cards) == 1
    assert p1.total == 3 and p2.total == 3  # total is the full count on every page
    assert keys(p1) | keys(p2) == {"RZ3", "RZ4", "RZ5"}  # complete
    assert keys(p1) & keys(p2) == set()  # disjoint


async def test_kind_filter_returns_only_that_stream_and_total_reflects_it():
    """P1 server filter: ``kind`` narrows the page to one stream so the FE need not
    over-fetch the other; ``total`` counts only the filtered kind."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    await _seed_run(
        spec,
        cid,
        cards={"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]},
        term_qs={"a.md": [TermQuestionDraft(term="R7", question="What is R7?")]},
    )
    cards_only = build_review_inbox(spec, actor=Actor.human("u"), kind="cards")
    assert len(cards_only.cards) == 1 and cards_only.questions == []
    assert cards_only.total == 1  # the question is excluded from the count

    qs_only = build_review_inbox(spec, actor=Actor.human("u"), kind="questions")
    assert qs_only.cards == [] and len(qs_only.questions) == 1
    assert qs_only.total == 1

    both = build_review_inbox(spec, actor=Actor.human("u"))  # kind defaults to "all"
    assert both.total == 2


async def test_q_filters_across_the_whole_stream_case_insensitively():
    """P1 server filter: ``q`` is a case-insensitive substring over a card's
    title/body/keys and a question's term/text/quote — applied to the *whole* set
    (so a match on page 5 still surfaces), not just the current page."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    await _seed_run(
        spec,
        cid,
        cards={
            "a.md": [
                CardDraft(keys=["RZ3"], title="RZ3", body="reflow zone three"),
                CardDraft(keys=["XY9"], title="XY9", body="gamma"),
            ]
        },
        term_qs={"a.md": [TermQuestionDraft(term="R7", question="What is R7?")]},
    )
    by_body = build_review_inbox(spec, actor=Actor.human("u"), q="Reflow")  # card body, mixed case
    assert [c.card.keys[0] for c in by_body.cards] == ["RZ3"]
    assert by_body.questions == [] and by_body.total == 1

    by_q = build_review_inbox(spec, actor=Actor.human("u"), q="r7")  # only the question
    assert by_q.cards == [] and [q.question.term for q in by_q.questions] == ["R7"]
    assert by_q.total == 1


async def test_actionable_filter_and_total_actionable_count():
    """P1: ``actionable=True`` keeps only rows the actor may write (the nav badge's
    "what can I act on"); ``total_actionable`` reports that count over the whole
    filtered set so the badge can read it from an empty (``limit=0``) page."""
    spec = make_spec(default_user="owner")
    coll_act = _collection(
        spec,
        "Act",
        owner="owner",
        permission=Permission(
            visibility="restricted", read_content=["user:u"], add_content=["user:u"]
        ),
    )
    coll_ro = _collection(
        spec,
        "RO",
        owner="owner",
        permission=Permission(visibility="restricted", read_content=["user:u"]),
    )
    coord = _coord(
        spec,
        {
            "act.md": [CardDraft(keys=["A"], title="A", body="a")],
            "ro.md": [CardDraft(keys=["B"], title="B", body="b")],
        },
        owner="owner",
    )
    da = _add_source(spec, coll_act, "act.md", "x", owner="owner")
    dr = _add_source(spec, coll_ro, "ro.md", "y", owner="owner")
    coord.enqueue(coll_act, [da])
    coord.enqueue(coll_ro, [dr])
    await coord.aclose()

    actor = Actor.human("u")
    full = build_review_inbox(spec, actor=actor)
    assert full.total == 2
    assert full.total_actionable == 1  # only the "Act" collection's card is writable by u

    only_act = build_review_inbox(spec, actor=actor, actionable=True)
    assert [c.card.keys[0] for c in only_act.cards] == ["A"]
    assert only_act.total == 1


def test_msgspec_roundtrip_of_the_inbox_structs():
    """The inbox structs serialise cleanly (they cross the wire via the route)."""
    from workspace_app.kb.review_inbox import ReviewInbox

    empty = ReviewInbox(cards=[], questions=[])
    assert msgspec.json.decode(msgspec.json.encode(empty), type=ReviewInbox) == empty


def _cluster_member(
    spec, cid, *, kind, ref_id, run_id="", cluster_key, state="active", reason="", label=""
):
    from workspace_app.resources.kb import ClusterMember

    spec.get_resource_manager(ClusterMember).create(
        ClusterMember(
            collection_id=cid,
            kind=kind,
            ref_id=ref_id,
            run_id=run_id,
            cluster_key=cluster_key,
            state=state,
            reason=reason,
            label=label,
        )
    )


async def test_grouped_inbox_merges_a_card_and_question_of_one_concept():
    """#506 P7: with grouped=True the inbox returns one ReviewCluster per concept —
    a proposal + a question the reconcile step put in one cluster collapse to a
    single row (⑤), and the flat cards/questions lists are empty (the FE reads
    clusters)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    await _seed_run(
        spec,
        cid,
        cards={"a.md": [CardDraft(keys=["RZ3"], title="RZ3", snippet="s")]},
        term_qs={"a.md": [TermQuestionDraft(term="R7", question="What is R7?")]},
    )
    flat = build_review_inbox(spec, actor=Actor.human("u"))
    run_id, card_id, qid = flat.cards[0].run_id, flat.cards[0].card.id, flat.questions[0].qid
    _cluster_member(spec, cid, kind="proposal", ref_id=card_id, run_id=run_id, cluster_key="rz3")
    _cluster_member(spec, cid, kind="term_question", ref_id=qid, cluster_key="rz3")

    grouped = build_review_inbox(spec, actor=Actor.human("u"), grouped=True)

    assert grouped.total == 1
    assert grouped.cards == [] and grouped.questions == []
    (cl,) = grouped.clusters
    assert cl.cluster_key == "rz3"
    assert len(cl.cards) == 1 and len(cl.questions) == 1


async def test_suppressed_audit_lists_dropped_candidates_with_reason():
    """#506 P7: the suppressed filter surfaces what the reconcile step auto-dropped
    (already explained) — each with its reason + label — so a human can audit that
    nothing was wrongly discarded. Dropped candidates aren't on any run, so they come
    straight from the suppressed ClusterMembers."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    _cluster_member(
        spec,
        cid,
        kind="proposal",
        ref_id="0",
        run_id="r1",
        cluster_key="alpha",
        state="suppressed",
        reason="wiki",
        label="Alpha",
    )

    audit = build_review_inbox(spec, actor=Actor.human("u"), suppressed=True)

    assert audit.cards == [] and audit.questions == [] and audit.clusters == []
    (s,) = audit.suppressed
    assert s.label == "Alpha"
    assert s.reason == "wiki"
    assert s.kind == "proposal"
    assert s.collection_name == "Alpha"


async def test_suppressed_audit_hidden_from_the_default_view():
    """A suppressed candidate never appears in the normal (active) inbox."""
    spec = make_spec(default_user="u")
    cid = _collection(spec, "Alpha")
    _cluster_member(
        spec, cid, kind="proposal", ref_id="0", run_id="r1", cluster_key="a", state="suppressed"
    )
    normal = build_review_inbox(spec, actor=Actor.human("u"))
    assert normal.cards == [] and normal.suppressed == []
