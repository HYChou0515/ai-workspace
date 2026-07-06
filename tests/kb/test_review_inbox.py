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

    def digest(self, *, doc_path: str, doc_text: str):
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
        {"one.md": [CardDraft(keys=["A"], title="A", snippet="s")],
         "two.md": [CardDraft(keys=["B"], title="B", snippet="s")]},
    )
    d1 = _add_source(spec, c1, "one.md", "x")
    d2 = _add_source(spec, c2, "two.md", "y")
    coord.enqueue(c1, [d1])
    coord.enqueue(c2, [d2])
    await coord.aclose()

    inbox = build_review_inbox(spec, actor=Actor.human("u"), collection_id=c1)
    assert {i.collection_id for i in inbox.cards} == {c1}
    assert [i.card.keys for i in inbox.cards] == [["A"]]


def test_msgspec_roundtrip_of_the_inbox_structs():
    """The inbox structs serialise cleanly (they cross the wire via the route)."""
    from workspace_app.kb.review_inbox import ReviewInbox

    empty = ReviewInbox(cards=[], questions=[])
    assert msgspec.json.decode(msgspec.json.encode(empty), type=ReviewInbox) == empty
