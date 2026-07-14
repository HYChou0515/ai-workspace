"""CardProposalStore — first-class CardProposal rows (#511).

Card-gen proposals used to live nested in ``CardGenRun.proposals`` (a msgspec
list field), so the 待審核 review inbox loaded EVERY run of a status into memory
and sliced the merged list in Python — O(all proposals) per page (the #511 "fake
pagination"). This store manages each proposal as its own :class:`CardProposal`
resource, addressed by the deterministic ``prop:{run}:{pid}`` id (shared with the
reconcile ClusterMember), so the review views can page via specstar's native
``order_by().offset().limit()``.

P2 makes this the SOLE store for the review lifecycle: the reviewer's per-proposal
writes (decide / drawer-edit / commit / whole-run dismiss) are compare-and-swap
``modify`` calls on the individual row (replacing ``CardGenRunStore``'s
read-modify-write over the nested list), and the coordinator + review inbox read
proposals back out of here. Whether a run is still in the 待審核 queue is no longer
a ``run.status`` terminal (``committed``/``dismissed`` are gone) — it's simply
"does the run still have an ACTIVE (pending/accepted) proposal", a count query.

Mirrors :class:`~workspace_app.kb.card_gen_run.CardGenRunStore` — a thin
stateless wrapper over the ``CardProposal`` resource manager; every caller
constructs its own ``CardProposalStore(spec)``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

import msgspec
from specstar import QB, SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    RevisionStatus,
)

from .card_gen import (
    _ACTIVE_DECISIONS,
    CardProposal,
    ProposedCard,
    card_proposal_id,
    card_proposal_to_proposed,
    proposal_to_card_proposal,
)

# TERMINAL decisions (a proposal that has left the queue): the history / resolved
# view; the complement of _ACTIVE_DECISIONS. "dismissed" reuses "rejected" (#511 P2
# decision: whole-run dismiss rejects every active proposal, no new enum value).
_TERMINAL_DECISIONS = ["committed", "rejected"]

# Per-run proposal contention is bounded by the run's proposal count; a generous
# backstop against a live-lock, mirroring CardGenRunStore.
_MAX_CAS_RETRIES = 1000


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

    # ── per-proposal review writes (#511 P2: CAS one row, not the nested list) ──
    def set_decision(self, proposal_id: str, decision: str) -> CardProposal | None:
        """Flip one proposal's review ``decision`` by id (inline accept/reject),
        under CAS. A no-op (returns ``None``) if the id isn't found."""
        return self._cas(proposal_id, lambda cp: msgspec.structs.replace(cp, decision=decision))

    def update(self, proposal_id: str, new_card: ProposedCard) -> CardProposal | None:
        """Replace one proposal's reviewer-editable content + decision by id (drawer
        edit), keeping its immutable ``collection_id``/``run_id`` refs + the id. A
        no-op if the id isn't found."""
        return self._cas(
            proposal_id,
            lambda cp: msgspec.structs.replace(
                cp,
                keys=list(new_card.keys),
                title=new_card.title,
                body=new_card.body,
                confident=new_card.confident,
                mode=new_card.mode,
                target_card_id=new_card.target_card_id,
                provenance=list(new_card.provenance),
                decision=new_card.decision,
            ),
        )

    def mark_committed(self, proposal_ids: list[str]) -> None:
        """Advance each referenced ACTIVE proposal to ``committed`` (its card was
        just written). An already-terminal ref is skipped (idempotent), so a
        redelivered commit writes no second transition."""
        for pid in proposal_ids:
            self._cas(
                pid,
                lambda cp: msgspec.structs.replace(cp, decision="committed")
                if cp.decision in _ACTIVE_DECISIONS
                else None,
            )

    def dismiss_run(self, run_id: str) -> int:
        """Whole-run dismiss: reject every ACTIVE proposal of the run (discard
        without writing cards), so the run drops out of the 待審核 queue. Returns how
        many were flipped (0 = already resolved → idempotent)."""
        flipped = 0
        for pid in self._active_proposal_ids_of_run(run_id):
            if self._cas(
                pid,
                lambda cp: msgspec.structs.replace(cp, decision="rejected")
                if cp.decision in _ACTIVE_DECISIONS
                else None,
            ):
                flipped += 1
        return flipped

    def replace_run_proposals(
        self, collection_id: str, run_id: str, proposals: list[ProposedCard]
    ) -> None:
        """Persist the reviewer's edited set for a run (the ``save_review`` bulk
        write) — upsert each proposal's row by its ``prop:{run}:{pid}`` id. Each is a
        ``create_or_update`` so it overwrites the stored content/decision with what
        the FE sent back."""
        for p in proposals:
            self._rm.create_or_update(
                card_proposal_id(run_id, p.id),
                proposal_to_card_proposal(collection_id, run_id, p),
            )

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

    # ── per-run reads (the coordinator's proposals()/commit iterate a run) ──
    def list_by_run(self, run_id: str) -> list[ProposedCard]:
        """All of a run's proposals as domain :class:`ProposedCard`\\ s (id = pid),
        oldest first (finalize creation order), so the coordinator's
        ``proposals(run_id)`` + commit iterate them like the old nested list."""
        query = (QB["run_id"] == run_id).sort(QB.created_time().asc(), QB.resource_id().asc())
        out: list[ProposedCard] = []
        for r in self._rm.list_resources(query.build()):
            d = r.data
            assert isinstance(d, CardProposal)  # rm is CardProposal-typed; narrows ty
            pid = _pid(r.info.resource_id, run_id)  # ty: ignore[unresolved-attribute]
            out.append(card_proposal_to_proposed(pid, d))
        return out

    def list_for_review(
        self, collection_id: str, *, resolved: bool = False
    ) -> list[tuple[str, float, ProposedCard]]:
        """ALL of a collection's review proposals (unpaged) as ``(run_id,
        created_epoch, ProposedCard)``, newest first — the ACTIVE (待審) set, or with
        ``resolved`` the TERMINAL (history) set. Used where the whole set is needed
        (the reconcile member backfill); the inbox pages via :meth:`page_for_review`."""
        rows, _total = self.page_for_review([collection_id], resolved=resolved)
        return [(run_id, created, card) for _cid, run_id, created, card in rows]

    def page_for_review(
        self,
        collection_ids: list[str],
        *,
        resolved: bool = False,
        q: str = "",
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[tuple[str, str, float, ProposedCard]], int]:
        """One page of the flat card stream ACROSS ``collection_ids`` as
        ``(collection_id, run_id, created_epoch, ProposedCard)``, newest first, plus
        the full filtered ``total`` (#511 P3).

        Without ``q`` this is a single ``in_(cids) & decision.in_(set)`` query paged
        at the DB (``offset``/``limit``) + a ``count_resources`` total — the native
        pagination that replaces the load-every-run "fake pagination". With ``q`` (a
        title/body/keys substring, which isn't DB-indexable) it's a bounded scan of
        those collections' proposal rows filtered in Python — still far lighter than
        the old load-all, since it reads only CardProposal rows, not every run's
        nested list. ``limit=None`` returns the whole offset-onward stream."""
        if not collection_ids:
            return [], 0
        decisions = _TERMINAL_DECISIONS if resolved else list(_ACTIVE_DECISIONS)
        base = QB["collection_id"].in_(collection_ids) & QB["decision"].in_(decisions)
        ordering = base.sort(QB.created_time().desc(), QB.resource_id().asc())
        if not q:
            total = self._rm.count_resources(base.build())
            paged = ordering.offset(offset)
            if limit is not None:
                paged = paged.limit(limit)
            rows = [_review_row(r) for r in self._rm.list_resources(paged.build())]
            return rows, total
        needle = q.lower()
        matched = [
            row
            for r in self._rm.list_resources(ordering.build())
            if _card_matches(needle, (row := _review_row(r))[3])
        ]
        total = len(matched)
        page = matched[offset:] if limit is None else matched[offset : offset + limit]
        return page, total

    def active_runs(self, collection_id: str) -> list[tuple[str, int]]:
        """The collection's runs that still hold ACTIVE proposals, as ``(run_id,
        active_count)``, newest first (by the run's newest active proposal) — the
        待審核 queue rows. Replaces ``run.status``-gated ``pending_for_collection``:
        a run is in the queue iff this yields it. P2 groups in Python over the active
        page; P4 pushes the GROUP BY down."""
        counts: dict[str, int] = {}
        order: list[str] = []
        for _, cp in self.list_active(collection_id):
            if cp.run_id not in counts:
                order.append(cp.run_id)  # first seen under the newest-first sort
            counts[cp.run_id] = counts.get(cp.run_id, 0) + 1
        return [(run_id, counts[run_id]) for run_id in order]

    # ── machinery ────────────────────────────────────────────────────
    def _active_proposal_ids_of_run(self, run_id: str) -> list[str]:
        """The ``prop:{run}:{pid}`` ids of a run's ACTIVE proposals — the dismiss
        target set."""
        query = (QB["run_id"] == run_id) & QB["decision"].in_(list(_ACTIVE_DECISIONS))
        return [
            r.info.resource_id  # ty: ignore[unresolved-attribute]
            for r in self._rm.list_resources(query.build())
        ]

    def _cas(
        self, proposal_id: str, mutate: Callable[[CardProposal], CardProposal | None]
    ) -> CardProposal | None:
        """Optimistic read-modify-write of one proposal row. ``mutate(cp)`` returns
        the next row, or ``None`` to abort with no write (an idempotent no-op).
        Retries on a concurrent writer until it wins or the row vanishes (mirrors
        ``CardGenRunStore._cas``)."""
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = self._rm.get(proposal_id)
            except ResourceIDNotFoundError:
                return None  # proposal cascaded away (run / collection deleted)
            cp = res.data
            assert isinstance(cp, CardProposal)
            new = mutate(cp)
            if new is None:
                return None
            try:
                self._rm.modify(
                    proposal_id,
                    new,
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                return new
            except PreconditionFailedError:
                continue  # another writer won the race — re-read and retry
        raise RuntimeError(  # pragma: no cover
            f"CardProposal CAS exhausted retries for {proposal_id}"
        )


def _pid(proposal_id: str, run_id: str) -> str:
    """Recover the per-run ``pid`` from a ``prop:{run}:{pid}`` resource id (the
    inverse of :func:`card_proposal_id`)."""
    return proposal_id.removeprefix(f"prop:{run_id}:")


def _review_row(r: object) -> tuple[str, str, float, ProposedCard]:
    """One review-inbox row ``(collection_id, run_id, created_epoch, ProposedCard)``
    from a stored CardProposal resource. Carries ``collection_id`` because a
    cross-collection page can't recover it from the id-less ProposedCard."""
    d = r.data  # ty: ignore[unresolved-attribute]
    assert isinstance(d, CardProposal)  # rm is CardProposal-typed; narrows ty
    pid = _pid(r.info.resource_id, d.run_id)  # ty: ignore[unresolved-attribute]
    return (
        d.collection_id,
        d.run_id,
        r.info.created_time.timestamp(),  # ty: ignore[unresolved-attribute]
        card_proposal_to_proposed(pid, d),
    )


def _card_matches(needle: str, card: ProposedCard) -> bool:
    """Whether a proposal's text contains the (already lower-cased) ``needle`` — the
    review inbox's free-text ``q`` filter over a card's title / body / keys (mirrors
    ``review_inbox._row_matches``)."""
    return any(needle in field.lower() for field in (card.title, card.body, *card.keys))
