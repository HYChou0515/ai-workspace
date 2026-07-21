"""#534 B — the vocabulary layer: shared identity, held as links.

A ``GraphEntity`` owns nothing. It says "these mentions are the same thing", and
it says it by pointing at them — so deciding wrongly costs a link, not a record.
The mentions it points at are untouched and stay readable exactly as their
documents wrote them, which is what makes a wrong grouping VISIBLE (an entry
holding evidence that does not belong) and free to undo.

Every link carries the basis it was made on, because a vocabulary nobody can
audit is a vocabulary nobody can trust: "the document said so, here" and "the
model thought they looked alike" are not the same claim, and the difference has
to survive into the record.
"""

from __future__ import annotations

import pytest
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from workspace_app.resources import make_spec
from workspace_app.resources.graph import (
    LINK_BASES,
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    mention_id,
)
from workspace_app.resources.kb import Collection


def _collection(spec: SpecStar, name: str = "c") -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using("bob"):
        return rm.create(Collection(name=name)).resource_id


def _mention(spec: SpecStar, cid: str, doc: str, surface: str) -> str:
    from workspace_app.kb.graph.normalize import norm_surface

    rm = spec.get_resource_manager(GraphMention)
    with rm.using("bob"):
        rm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id=doc,
                surface=surface,
                norm_surface=norm_surface(surface),
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id(doc, surface),
        )
    return mention_id(doc, surface)


def _entity(spec: SpecStar, name: str, *, collections: list[str] | None = None) -> str:
    rm = spec.get_resource_manager(GraphEntity)
    with rm.using("bob"):
        return rm.create(
            GraphEntity(canonical_name=name, collection_ids=collections or [])
        ).resource_id


class TestLinkBasis:
    def test_a_link_records_what_it_was_made_on(self):
        spec = make_spec(default_user=lambda: "bob")
        cid = _collection(spec)
        mid = _mention(spec, cid, "deck-A", "回焊爐")
        eid = _entity(spec, "回焊爐", collections=[cid])
        rm = spec.get_resource_manager(GraphEntityLink)
        with rm.using("bob"):
            rm.create(
                GraphEntityLink(
                    entity_id=eid, mention_id=mid, basis="declared", evidence="deck-A#3"
                )
            )
        (got,) = list(rm.list_resources((QB["entity_id"] == eid).build()))
        assert isinstance(got.data, GraphEntityLink)
        assert got.data.basis == "declared"
        assert got.data.evidence == "deck-A#3"

    def test_the_bases_are_ordered_from_checkable_to_merely_plausible(self):
        """The order IS the policy: everything above the model's own opinion can
        point at something a person could go and verify — a rule, a sentence in a
        document, an earlier human decision. Only the last one cannot, which is
        why only the last one waits for review."""
        assert LINK_BASES.index("identical") < LINK_BASES.index("declared")
        assert LINK_BASES.index("declared") < LINK_BASES.index("approved")
        assert LINK_BASES[-1] == "resembles"

    def test_a_pending_link_is_not_yet_part_of_the_entity(self):
        """A proposal is stored, not applied — it is what the review queue reads.
        Until someone accepts it, the entity does not claim that mention."""
        spec = make_spec(default_user=lambda: "bob")
        cid = _collection(spec)
        mid = _mention(spec, cid, "deck-A", "RO-3")
        eid = _entity(spec, "回焊爐", collections=[cid])
        rm = spec.get_resource_manager(GraphEntityLink)
        with rm.using("bob"):
            rm.create(
                GraphEntityLink(entity_id=eid, mention_id=mid, basis="resembles", state="pending")
            )
        active = list(rm.list_resources((QB["state"] == "active").build()))
        assert active == []


class TestEntityVisibility:
    """Identity is shared across corpora, so it cannot inherit one collection's
    permission. It is visible iff the caller can read a collection it has evidence
    in — the entity carries that list, because an access scope is a predicate over
    ONE row and cannot ask another table what the caller may see."""

    def test_an_entity_is_visible_through_a_readable_collection(self):
        spec = make_spec(default_user=lambda: "bob")
        cid = _collection(spec)
        eid = _entity(spec, "回焊爐", collections=[cid])
        rm = spec.get_resource_manager(GraphEntity)
        with rm.using("alice", apply_access_scope=True):  # ty: ignore[unknown-argument]
            assert rm.get(eid).data is not None

    def test_an_entity_whose_evidence_is_all_unreadable_is_hidden(self):
        from workspace_app.perm import Permission

        spec = make_spec(default_user=lambda: "bob")
        crm = spec.get_resource_manager(Collection)
        with crm.using("bob"):
            cid = crm.create(
                Collection(name="secret", permission=Permission(visibility="private"))
            ).resource_id
        eid = _entity(spec, "機密製程", collections=[cid])
        rm = spec.get_resource_manager(GraphEntity)
        with (
            rm.using("alice", apply_access_scope=True),  # ty: ignore[unknown-argument]
            pytest.raises(ResourceIDNotFoundError),
        ):
            rm.get(eid)
        with rm.using("bob", apply_access_scope=True):  # ty: ignore[unknown-argument]
            assert rm.get(eid).data is not None

    def test_an_entity_with_no_evidence_yet_is_hidden(self):
        """An empty list means nothing vouches for this identity, so nobody sees
        it — the same fail-closed default the evidence layer uses. A name alone
        can leak (a customer code, an unreleased part), so it must not appear on
        the strength of existing."""
        spec = make_spec(default_user=lambda: "bob")
        eid = _entity(spec, "回焊爐", collections=[])
        rm = spec.get_resource_manager(GraphEntity)
        with (
            rm.using("bob", apply_access_scope=True),  # ty: ignore[unknown-argument]
            pytest.raises(ResourceIDNotFoundError),
        ):
            rm.get(eid)

    def test_evidence_in_any_readable_collection_is_enough(self):
        from workspace_app.perm import Permission

        spec = make_spec(default_user=lambda: "bob")
        crm = spec.get_resource_manager(Collection)
        with crm.using("bob"):
            secret = crm.create(
                Collection(name="secret", permission=Permission(visibility="private"))
            ).resource_id
        with crm.using("amy"):
            open_one = crm.create(Collection(name="open")).resource_id
        eid = _entity(spec, "回焊爐", collections=[secret, open_one])
        rm = spec.get_resource_manager(GraphEntity)
        with rm.using("alice", apply_access_scope=True):  # ty: ignore[unknown-argument]
            assert rm.get(eid).data is not None
