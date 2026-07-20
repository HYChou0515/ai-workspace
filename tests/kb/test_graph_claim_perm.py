"""#534 slice 2 — a metric claim inherits the read permission of the deck it came from.

A ``GraphClaim`` row IS content: a metric name and its value, lifted out of one
slide. So it must be exactly as visible as the document it was extracted from —
no more. Two layers decide that, and the claim carries a denormalized mirror of
both so the filter runs at the storage layer (hiding the claim on EVERY path,
including the auto-CRUD ``GET /graph-claim`` that no hand-written route guards):

* the parent collection's visibility (the #303 mirror, same three fields a
  SourceDoc carries), and
* the deck's OWN per-doc tightening (#308), which can restrict a single deck
  below its collection.

The doc half gates on ``read_content``, NOT ``read_meta`` as the SourceDoc scope
does: ``read_meta`` answers "may you know this deck exists", and a claim reveals
far more than existence.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim
from workspace_app.resources.kb import Collection


def _collection(spec: SpecStar, *, permission: Permission | None = None, by: str = "bob") -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(by):
        return rm.create(Collection(name="c", permission=permission)).resource_id


def _claim(
    spec: SpecStar,
    cid: str,
    *,
    collection_visibility: str = "public",
    collection_read_meta: list[str] | None = None,
    collection_created_by: str = "bob",
    doc_visibility: str = "public",
    doc_read_content: list[str] | None = None,
    by: str = "bob",
) -> str:
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using(by):
        return rm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-1",
                norm_metric="revenue",
                metric="Revenue",
                value="1.2M",
                collection_visibility=collection_visibility,
                collection_read_meta=collection_read_meta or [],
                collection_created_by=collection_created_by,
                doc_visibility=doc_visibility,
                doc_read_content=doc_read_content or [],
            )
        ).resource_id


def _readable(spec: SpecStar, user: str, claim_id: str) -> bool:
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using(user, apply_access_scope=True):  # ty: ignore[unknown-argument]
        try:
            rm.get(claim_id)
        except ResourceIDNotFoundError:
            return False
        return True


def test_a_claim_from_a_private_collection_is_hidden_from_a_non_owner():
    """The collection half of the mirror: the same predicate that hides a
    collection hides the metrics extracted out of it — at the storage layer, so
    the unguarded auto-CRUD route is covered too."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec, permission=Permission(visibility="private"))
    claim_id = _claim(spec, cid, collection_visibility="private")
    assert _readable(spec, "bob", claim_id) is True
    assert _readable(spec, "alice", claim_id) is False


def test_a_claim_from_a_restricted_deck_is_hidden_even_when_the_collection_is_open():
    """The doc half: a deck tightened below its collection takes its numbers with
    it. Without this, alice cannot open the deck but still reads "Revenue 1.2M"
    off it — the door locked and the window left open."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    claim_id = _claim(spec, cid, doc_visibility="restricted", doc_read_content=[])
    assert _readable(spec, "alice", claim_id) is False


def test_a_restricted_deck_still_shows_its_claims_to_a_granted_reader():
    """The tightening is a grant list, not a blanket: whoever the deck grants
    ``read_content`` reads its claims."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    claim_id = _claim(spec, cid, doc_visibility="restricted", doc_read_content=["user:alice"])
    assert _readable(spec, "alice", claim_id) is True


def test_a_deck_with_no_tightening_leaves_the_collection_verdict_alone():
    """A deck that adds no restriction of its own mirrors ``doc_visibility="public"``
    — the verdict, stated explicitly, exactly as ``collection_mirror_fields`` always
    writes all three of its fields rather than leaving them to be inferred. The doc
    half then PASSES and the override can only ever tighten."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    claim_id = _claim(spec, cid)
    assert _readable(spec, "alice", claim_id) is True


def test_a_claim_whose_mirror_was_never_written_is_hidden():
    """The struct defaults are empty strings, and empty is NOT a verdict: a writer
    that forgets the mirror must produce an INVISIBLE claim, never a public one.
    Fail closed — the default has to be the safe direction, because the unsafe one
    fails silently (a leak nobody reports) while this one fails loudly (a missing
    row someone chases)."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("bob"):
        claim_id = rm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-1",
                norm_metric="revenue",
                metric="Revenue",
                value="1.2M",
            )
        ).resource_id
    assert _readable(spec, "alice", claim_id) is False
    assert _readable(spec, "bob", claim_id) is False


def test_the_doc_half_gates_on_read_content_not_read_meta():
    """A reader granted only ``read_meta`` may know the deck exists; that must NOT
    hand them its numbers. This is the one place the claim scope deliberately
    differs from ``source_doc_access_scope``."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    claim_id = _claim(
        spec,
        cid,
        doc_visibility="restricted",
        doc_read_content=[],
    )
    assert _readable(spec, "alice", claim_id) is False


@pytest.mark.parametrize("user", ["root"])
def test_a_superuser_sees_every_claim(user: str):
    """The single greppable see-everything path, same as every other scope."""
    spec = make_spec(default_user=lambda: "bob", superusers=frozenset({"root"}))
    cid = _collection(spec, permission=Permission(visibility="private"))
    claim_id = _claim(spec, cid, collection_visibility="private", doc_visibility="restricted")
    assert _readable(spec, user, claim_id) is True


def _claim_row(spec: SpecStar, claim_id: str) -> GraphClaim:
    got = spec.get_resource_manager(GraphClaim).get(claim_id).data
    assert isinstance(got, GraphClaim)
    return got


def test_pushing_a_collection_mirror_reaches_its_claims():
    """A collection's permission change has to travel all the way down. The claim
    mirror is a COPY, so a collection tightened after extraction leaves every claim
    on the old, looser verdict until the fan-out re-pushes it."""
    from workspace_app.kb.doc_permission import push_mirror_to_claims

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    claim_id = _claim(spec, cid)
    assert _readable(spec, "alice", claim_id) is True

    moved = push_mirror_to_claims(
        spec, cid, visibility="restricted", read_meta=["user:amy"], created_by="bob"
    )
    assert moved == 1
    assert _claim_row(spec, claim_id).collection_visibility == "restricted"
    assert _readable(spec, "alice", claim_id) is False
    assert _readable(spec, "amy", claim_id) is True


def test_pushing_a_collection_mirror_twice_moves_nothing_the_second_time():
    """A merge patch that changes nothing creates no revision, so re-running the
    fan-out is cheap and reports honestly that it moved no rows."""
    from workspace_app.kb.doc_permission import push_mirror_to_claims

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    _claim(spec, cid)
    kwargs = {"visibility": "restricted", "read_meta": [], "created_by": "bob"}
    assert push_mirror_to_claims(spec, cid, **kwargs) == 1
    assert push_mirror_to_claims(spec, cid, **kwargs) == 0


def test_pushing_a_deck_override_reaches_only_that_decks_claims():
    """The doc half is keyed on the deck, not the collection: tightening one deck
    must not touch its neighbours' metrics."""
    from workspace_app.kb.doc_permission import push_doc_override_to_claims

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    mine = _claim(spec, cid)
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("bob"):
        other = rm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-2",
                norm_metric="revenue",
                metric="Revenue",
                value="9M",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        ).resource_id

    push_doc_override_to_claims(
        spec, "deck-1", visibility="restricted", read_content=[], created_by="bob"
    )
    assert _readable(spec, "alice", mine) is False
    assert _readable(spec, "alice", other) is True


def test_clearing_a_deck_override_reopens_its_claims():
    """Clearing an override reverts the deck to pure inheritance, so the mirror has
    to be rewritten to the explicit "public" verdict — not left on the old value,
    and not blanked (blank hides the row)."""
    from workspace_app.kb.doc_permission import push_doc_override_to_claims

    spec = make_spec(default_user=lambda: "bob")
    cid = _collection(spec)
    claim_id = _claim(spec, cid, doc_visibility="restricted", doc_read_content=[])
    assert _readable(spec, "alice", claim_id) is False

    push_doc_override_to_claims(
        spec, "deck-1", visibility="public", read_content=[], created_by="bob"
    )
    assert _claim_row(spec, claim_id).doc_visibility == "public"
    assert _readable(spec, "alice", claim_id) is True
