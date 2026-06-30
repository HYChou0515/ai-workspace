"""CodeWikiBuildRunStore — CAS join state for the code-wiki build fan-out (#281
P4). Mirrors :mod:`workspace_app.kb.index_run` (the #227 index fan-out).

A code-wiki build's L0 cards are fanned out into many small ``code_card`` jobs
(one per ``plan_card_batches`` batch). Those jobs run independently — possibly on
different pods, in parallel (``partition_key=None``) — so "every card is built"
is a fact no single job owns. This store is the agreement point, and it is
deliberately **queue-agnostic**: correctness rests on compare-and-swap against
the resource's etag, NOT on the queue's ``partition_key`` (which the RabbitMQ
backend ignores).

The finalize trigger is NOT "whoever recorded the last batch" — that races and
loses on crash. Every finisher re-evaluates the gate ``done ∪ failed == total``
and races to flip ``finalized`` under CAS; exactly one wins and enqueues the
finalize. A crash before finalize just leaves the gate open for the next
finisher (or a manual rebuild) to re-drive — finalize is idempotent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import msgspec
from specstar import SpecStar
from specstar.types import PreconditionFailedError, ResourceIDNotFoundError, RevisionStatus

from ...resources import CodeWikiBuildRun

_LOGGER = logging.getLogger(__name__)

# Contention is bounded by the batch count; a generous backstop against live-lock.
_MAX_CAS_RETRIES = 1000


class CodeWikiBuildRunStore:
    """Read/CAS-write the per-collection :class:`CodeWikiBuildRun` join state."""

    def __init__(self, spec: SpecStar) -> None:
        self._rm = spec.get_resource_manager(CodeWikiBuildRun)

    # ── seed / read ──────────────────────────────────────────────────
    def start(self, collection_id: str, total: int, *, phase: str = "cards") -> None:
        """Seed (or reset) the run for a fresh build — overwrites any prior
        terminal run. ``total`` is the card-batch count; ``phase`` is the coarse
        activity shown by the FE (#355 seeds it ``"cloning"`` for the pre-build
        code-sync, then ``code_split`` re-seeds with the default ``"cards"``).
        Written as a draft revision so the card jobs can ``modify`` it under CAS."""
        run = CodeWikiBuildRun(collection_id=collection_id, total=total, phase=phase)
        self._rm.create_or_update(collection_id, run, status=RevisionStatus.draft)

    def set_phase(self, collection_id: str, phase: str) -> None:
        """Advance the coarse build phase (#355: ``cloning`` → ``ingesting``)
        under CAS, so the FE's status poll reflects the live sync activity."""
        self._cas(collection_id, lambda run: msgspec.structs.replace(run, phase=phase))

    def get(self, collection_id: str) -> CodeWikiBuildRun | None:
        try:
            data = self._rm.get(collection_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, CodeWikiBuildRun)  # narrow Struct | Unset for ty
        return data

    def is_active(self, collection_id: str) -> bool:
        """True while a build is in flight for this collection — the
        queue-agnostic coalescing guard, so a second trigger doesn't trample an
        in-flight build (card jobs are ``partition_key=None`` so the queue can't
        provide this)."""
        run = self.get(collection_id)
        return run is not None and run.status == "running"

    # ── CAS mutations ────────────────────────────────────────────────
    def mark_done(self, collection_id: str, batch_index: int) -> None:
        """Idempotently record that batch ``batch_index`` built OK. Re-running the
        same card job (at-least-once redelivery) is a no-op."""
        self._cas(collection_id, lambda run: _with_index(run, "done", batch_index))

    def mark_failed(self, collection_id: str, batch_index: int) -> None:
        """Idempotently record that batch ``batch_index`` gave up. Counts toward
        the finalize gate so a failed batch can't wedge the build forever."""
        self._cas(collection_id, lambda run: _with_index(run, "failed", batch_index))

    def claim_finalize(self, collection_id: str) -> bool:
        """Try to win the exactly-once finalize gate. Returns ``True`` for the
        single winner — only when every batch is accounted for
        (``done ∪ failed == total``) and the flag is not already claimed. Also
        flips ``phase`` to ``finalizing`` for the winner so the FE shows the
        right activity."""
        claimed = False

        def mutate(run: CodeWikiBuildRun) -> CodeWikiBuildRun | None:
            nonlocal claimed
            if run.finalized:
                return None  # already claimed by someone else
            if len(set(run.done) | set(run.failed)) < run.total:
                return None  # not every batch is accounted for yet
            claimed = True
            return msgspec.structs.replace(run, finalized=True, phase="finalizing")

        self._cas(collection_id, mutate)
        return claimed

    def finish(self, collection_id: str, *, status: str) -> None:
        """Stamp the terminal status (``done`` / ``error``) once finalize has run
        — this is what closes the active-run guard."""
        self._cas(collection_id, lambda run: msgspec.structs.replace(run, status=status))

    # ── machinery ────────────────────────────────────────────────────
    def _cas(
        self,
        collection_id: str,
        mutate: Callable[[CodeWikiBuildRun], CodeWikiBuildRun | None],
    ) -> CodeWikiBuildRun | None:
        """Optimistic read-modify-write. ``mutate(run)`` returns the next run, or
        ``None`` to abort with no write (an idempotent no-op / a closed gate).
        Retries on a concurrent writer until it wins or the run vanishes."""
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = self._rm.get(collection_id)
            except ResourceIDNotFoundError:
                return None  # run cascaded away (collection deleted mid-flight)
            run = res.data
            assert isinstance(run, CodeWikiBuildRun)
            new = mutate(run)
            if new is None:
                return None
            try:
                self._rm.modify(
                    collection_id,
                    new,
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                return new
            except (
                PreconditionFailedError
            ):  # pragma: no cover — concurrent-writer race, not deterministically reproducible
                continue  # another writer won the race — re-read and retry
        raise RuntimeError(  # pragma: no cover — backstop against live-lock
            f"CodeWikiBuildRun CAS exhausted retries for {collection_id}"
        )


def _with_index(
    run: CodeWikiBuildRun, field_name: str, batch_index: int
) -> CodeWikiBuildRun | None:
    """Append ``batch_index`` to ``run.{field_name}`` unless already present
    (idempotent). Returns ``None`` to signal "no change" so the CAS write is
    skipped entirely."""
    current: list[int] = getattr(run, field_name)
    if batch_index in current:
        return None
    return msgspec.structs.replace(run, **{field_name: [*current, batch_index]})
