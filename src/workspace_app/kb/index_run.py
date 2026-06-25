"""IndexRunStore — CAS join state for the index fan-out (#227).

A large index is fanned out into many small ``IndexJob(kind="process")`` jobs
(one per unit batch) so none exceeds the broker's consumer-ack timeout. Those
jobs run independently — possibly on different pods, possibly in parallel — so
"the whole doc is done" is a fact no single job owns. This store is the
agreement point, and it is deliberately **queue-agnostic**: correctness rests on
compare-and-swap against the resource's etag, NOT on the queue's ``partition_key``
serialization (which the RabbitMQ backend silently ignores).

The finalize trigger is NOT "whoever recorded the last batch" — that races (two
finishers can both see the set fill) and loses on crash (the last recorder can
die before triggering). Instead every finisher (and the safety sweep)
re-evaluates the gate ``done ∪ failed == total`` and races to flip the
``finalized`` flag under CAS; exactly one wins and triggers finalize, and a
crash before finalize just leaves the gate open for the next finisher (or the
sweep) to win.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import msgspec
from specstar import SpecStar
from specstar.types import PreconditionFailedError, ResourceIDNotFoundError, RevisionStatus

from ..resources import IndexRun

_LOGGER = logging.getLogger(__name__)

# Per-doc contention is bounded by the batch count; this is a generous backstop
# against a live-lock, not a tuning knob.
_MAX_CAS_RETRIES = 1000


class IndexRunStore:
    """Read/CAS-write the per-doc :class:`IndexRun` join state."""

    def __init__(self, spec: SpecStar) -> None:
        self._rm = spec.get_resource_manager(IndexRun)

    # ── seed / read ──────────────────────────────────────────────────
    def start(self, doc_id: str, collection_id: str, total: int) -> None:
        """Seed (or reset) the run for a fresh fan-out — overwrites any prior
        terminal run for this doc. Written as a draft revision so the process
        jobs can ``modify`` it in place under CAS (modify requires draft)."""
        run = IndexRun(doc_id=doc_id, collection_id=collection_id, total=total)
        self._rm.create_or_update(doc_id, run, status=RevisionStatus.draft)

    def get(self, doc_id: str) -> IndexRun | None:
        try:
            data = self._rm.get(doc_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, IndexRun)  # narrow Struct | Unset for ty
        return data

    def is_active(self, doc_id: str) -> bool:
        """True while a fan-out is in flight for this doc — the queue-agnostic
        coalescing guard, so a second reindex doesn't trample an in-flight one."""
        run = self.get(doc_id)
        return run is not None and run.status == "running"

    # ── CAS mutations ────────────────────────────────────────────────
    def mark_done(self, doc_id: str, batch_index: int) -> None:
        """Idempotently record that batch ``batch_index`` finished OK. Re-running
        the same process job (at-least-once redelivery) is a no-op."""
        self._cas(doc_id, lambda run: _with_index(run, "done", batch_index))

    def mark_failed(self, doc_id: str, batch_index: int) -> None:
        """Idempotently record that batch ``batch_index`` gave up (dead-lettered
        / non-retryable). Counts toward the finalize gate so a failed batch can't
        wedge the doc in ``indexing`` forever."""
        self._cas(doc_id, lambda run: _with_index(run, "failed", batch_index))

    def claim_finalize(self, doc_id: str) -> bool:
        """Try to win the exactly-once finalize gate. Returns ``True`` for the
        single winner — only when every batch is accounted for
        (``done ∪ failed == total``) and the flag is not already claimed. All
        other finishers (and the sweep) get ``False``. Crash-safe: if the winner
        dies before finalizing, the flag stays set but the finalize work is
        re-driven by redelivery; if it dies before even claiming, the gate is
        still open for the next caller."""
        claimed = False

        def mutate(run: IndexRun) -> IndexRun | None:
            nonlocal claimed
            if run.finalized:
                return None  # already claimed by someone else
            if len(set(run.done) | set(run.failed)) < run.total:
                return None  # not every batch is accounted for yet
            claimed = True
            return msgspec.structs.replace(run, finalized=True)

        self._cas(doc_id, mutate)
        return claimed

    def finish(self, doc_id: str, *, status: str) -> None:
        """Stamp the terminal status (``done`` / ``error``) once finalize has run
        — this is what closes the active-run guard."""
        self._cas(doc_id, lambda run: msgspec.structs.replace(run, status=status))

    # ── machinery ────────────────────────────────────────────────────
    def _cas(self, doc_id: str, mutate: Callable[[IndexRun], IndexRun | None]) -> IndexRun | None:
        """Optimistic read-modify-write. ``mutate(run)`` returns the next run, or
        ``None`` to abort with no write (an idempotent no-op or a closed gate).
        Retries on a concurrent writer until it wins or the run vanishes."""
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = self._rm.get(doc_id)
            except ResourceIDNotFoundError:
                return None  # run cascaded away (doc deleted mid-flight)
            run = res.data
            assert isinstance(run, IndexRun)
            new = mutate(run)
            if new is None:
                return None
            try:
                self._rm.modify(
                    doc_id,
                    new,
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                return new
            except PreconditionFailedError:
                continue  # another writer won the race — re-read and retry
        raise RuntimeError(f"IndexRun CAS exhausted retries for {doc_id}")  # pragma: no cover


def _with_index(run: IndexRun, field_name: str, batch_index: int) -> IndexRun | None:
    """Append ``batch_index`` to ``run.{field_name}`` unless already present
    (idempotent). Returns ``None`` to signal "no change" so the CAS write is
    skipped entirely."""
    current: list[int] = getattr(run, field_name)
    if batch_index in current:
        return None
    return msgspec.structs.replace(run, **{field_name: [*current, batch_index]})
