"""CardProposalStore — first-class CardProposal rows (#511 P1).

Card-gen proposals used to live nested in ``CardGenRun.proposals`` (a msgspec
list field), so the 待審核 review inbox loaded EVERY run of a status into memory
and sliced the merged list in Python — O(all proposals) per page (the #511 "fake
pagination"). This store manages each proposal as its own :class:`CardProposal`
resource, addressed by the deterministic ``prop:{run}:{pid}`` id (shared with the
reconcile ClusterMember), so the review views can page via specstar's native
``order_by().offset().limit()``.

Mirrors :class:`~workspace_app.kb.card_gen_run.CardGenRunStore` — a thin
stateless wrapper over the ``CardProposal`` resource manager; every caller
constructs its own ``CardProposalStore(spec)``.
"""

from __future__ import annotations

import contextlib

from specstar import QB, SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    RevisionStatus,
)

from .card_gen import (
    _ACTIVE_DECISIONS,
    CardProposal,
    ProposedCard,
    card_proposal_id,
    proposal_to_card_proposal,
)


class CardProposalStore:
    """Read/write the first-class :class:`CardProposal` rows (#511)."""

    def __init__(self, spec: SpecStar) -> None:
        self._rm = spec.get_resource_manager(CardProposal)

    def create_from_proposal(self, collection_id: str, run_id: str, p: ProposedCard) -> str:
        """Create the CardProposal row for one kept proposal, addressed by the
        deterministic ``prop:{run}:{pid}`` id, and return that id. Written as a
        draft so per-proposal review edits (#511 P2) can ``modify`` it under CAS.

        Idempotent (``if_not_exists``): a re-driven finalize (at-least-once
        redelivery) is a no-op on the existing row rather than an error — and
        crucially does NOT clobber it, so a reviewer's decision survives a
        re-finalize. Finalize proposals are deterministic, so the preserved
        content matches anyway."""
        cp = proposal_to_card_proposal(collection_id, run_id, p)
        rid = card_proposal_id(run_id, p.id)
        # A prior finalize already created it → leave the existing row untouched
        # (first-write-wins preserves any reviewer decision made since).
        with contextlib.suppress(DuplicateResourceError):
            # if_not_exists is a real create() kwarg as of specstar 0.11.15 (atomic
            # create-only → DuplicateResourceError on conflict); the ABC stub ty sees
            # doesn't declare it yet.
            self._rm.create(
                cp,
                resource_id=rid,
                status=RevisionStatus.draft,
                if_not_exists=True,  # ty: ignore[unknown-argument]
            )
        return rid

    def get(self, proposal_id: str) -> CardProposal | None:
        try:
            data = self._rm.get(proposal_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, CardProposal)  # narrow Struct | Unset for ty
        return data

    # ── 待審核 flat view: native DB pagination (#511) ────────────────
    def list_active(
        self, collection_id: str, *, offset: int = 0, limit: int | None = None
    ) -> list[tuple[str, CardProposal]]:
        """One page of a collection's ACTIVE (pending/accepted) proposals as
        ``(proposal_id, CardProposal)``, newest first — a real
        ``order_by().offset().limit()`` DB query, not load-all-then-slice. Sorted
        by the IMMUTABLE ``created_time`` then ``resource_id`` so a decision made
        between page fetches can't shift the window (same stability trick as the
        doc list)."""
        ordering = self._active_query(collection_id).sort(
            QB.created_time().desc(), QB.resource_id().asc()
        )
        paged = ordering.offset(offset)
        if limit is not None:
            paged = paged.limit(limit)
        # list_resources (not search_resources) returns the full SearchedResource
        # — the review inbox needs the proposal's title/body/keys, which aren't
        # indexed; the sort/offset/limit on the query still page it at the DB.
        out: list[tuple[str, CardProposal]] = []
        for r in self._rm.list_resources(paged.build()):
            d = r.data
            assert isinstance(d, CardProposal)  # rm is CardProposal-typed; narrows ty
            out.append((r.info.resource_id, d))  # ty: ignore[unresolved-attribute]
        return out

    def count_active(self, collection_id: str) -> int:
        """The pager total — a collection's ACTIVE proposal count, independent of
        any page's offset/limit."""
        return self._rm.count_resources(self._active_query(collection_id).build())

    @staticmethod
    def _active_query(collection_id: str):
        """``(collection_id == cid) AND decision ∈ {pending, accepted}`` — the
        待審核 predicate, both indexed so it never scans."""
        return (QB["collection_id"] == collection_id) & QB["decision"].in_(list(_ACTIVE_DECISIONS))
