"""EvalRunStore — CAS join state + per-batch staging for the eval fan-out (#535).

A mirror of ``IndexRunStore`` (#227), keyed by (collection, run) instead of doc:
``split`` seeds the run before enqueuing any batch; each ``batch`` idempotently
records its index and stages its ranks; ``finalize`` is won by exactly one
finisher racing the ``finalized`` flag under compare-and-swap (never "whoever was
last", which races and loses on crash). Correctness rests on CAS against the
etag, not on the queue's ``partition_key``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import msgspec
from specstar import QB, SpecStar
from specstar.types import PreconditionFailedError, ResourceIDNotFoundError, RevisionStatus

from ...resources import EvalBatchStat, EvalRun
from ...resources.eval import eval_batch_stat_id, eval_run_id

_LOGGER = logging.getLogger(__name__)
_MAX_CAS_RETRIES = 1000


class EvalRunStore:
    def __init__(self, spec: SpecStar) -> None:
        self._rm = spec.get_resource_manager(EvalRun)
        self._stat_rm = spec.get_resource_manager(EvalBatchStat)

    # ── seed / read ──────────────────────────────────────────────────
    def start(
        self,
        collection_id: str,
        run_label: str,
        total: int,
        *,
        seed: str = "",
        sample_size: int = 0,
    ) -> None:
        """Seed the run for a fresh fan-out (draft, so batches can CAS-modify it).
        ``seed`` / ``sample_size`` are run-level metadata finalize stamps onto the
        ``EvalResult``."""
        rid = eval_run_id(collection_id, run_label)
        run = EvalRun(
            collection_id=collection_id,
            run_label=run_label,
            total=total,
            seed=seed,
            sample_size=sample_size,
        )
        _LOGGER.info("eval_run: seed %s/%s total=%d", collection_id, run_label, total)
        self._rm.create_or_update(rid, run, status=RevisionStatus.draft)

    def get(self, collection_id: str, run_label: str) -> EvalRun | None:
        try:
            data = self._rm.get(eval_run_id(collection_id, run_label)).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, EvalRun)
        return data

    # ── per-batch staging ────────────────────────────────────────────
    def stage_batch(
        self,
        collection_id: str,
        run_label: str,
        batch_index: int,
        *,
        chunk_ranks: list[int | None],
        doc_ranks: list[int | None],
        n_kept: int,
        n_dropped: int,
    ) -> None:
        """Persist one batch's ranks (idempotent by id — a redelivered batch just
        overwrites the same staging row)."""
        rid = eval_batch_stat_id(collection_id, run_label, batch_index)
        self._stat_rm.create_or_update(
            rid,
            EvalBatchStat(
                collection_id=collection_id,
                run_label=run_label,
                batch_index=batch_index,
                chunk_ranks=chunk_ranks,
                doc_ranks=doc_ranks,
                n_kept=n_kept,
                n_dropped=n_dropped,
            ),
        )

    def read_batches(self, collection_id: str, run_label: str) -> list[EvalBatchStat]:
        """Every staged batch for this run (finalize rejoins them)."""
        query = ((QB["collection_id"] == collection_id) & (QB["run_label"] == run_label)).build()
        out: list[EvalBatchStat] = []
        for r in self._stat_rm.list_resources(query):
            data = r.data
            assert isinstance(data, EvalBatchStat)
            out.append(data)
        return out

    def clear_batches(self, collection_id: str, run_label: str) -> None:
        """Delete a run's staging rows once finalize has folded them in (delete by
        the deterministic id, so no meta round-trip)."""
        for b in self.read_batches(collection_id, run_label):
            self._stat_rm.delete(eval_batch_stat_id(collection_id, run_label, b.batch_index))

    # ── CAS mutations ────────────────────────────────────────────────
    def mark_done(self, collection_id: str, run_label: str, batch_index: int) -> None:
        self._cas(collection_id, run_label, lambda run: _with_index(run, "done", batch_index))

    def mark_failed(self, collection_id: str, run_label: str, batch_index: int) -> None:
        self._cas(collection_id, run_label, lambda run: _with_index(run, "failed", batch_index))

    def claim_finalize(self, collection_id: str, run_label: str) -> bool:
        """Win the exactly-once finalize gate: only when ``done ∪ failed == total``
        and not already claimed. Exactly one finisher gets ``True``."""
        claimed = False

        def mutate(run: EvalRun) -> EvalRun | None:
            nonlocal claimed
            if run.finalized:
                return None
            if len(set(run.done) | set(run.failed)) < run.total:
                return None
            claimed = True
            return msgspec.structs.replace(run, finalized=True)

        self._cas(collection_id, run_label, mutate)
        return claimed

    def finish(self, collection_id: str, run_label: str, *, status: str) -> None:
        self._cas(collection_id, run_label, lambda run: msgspec.structs.replace(run, status=status))

    # ── machinery ────────────────────────────────────────────────────
    def _cas(
        self,
        collection_id: str,
        run_label: str,
        mutate: Callable[[EvalRun], EvalRun | None],
    ) -> None:
        rid = eval_run_id(collection_id, run_label)
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = self._rm.get(rid)
            except ResourceIDNotFoundError:
                return  # run vanished (collection deleted mid-flight)
            run = res.data
            assert isinstance(run, EvalRun)
            new = mutate(run)
            if new is None:
                return
            try:
                self._rm.modify(
                    rid,
                    new,
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                return
            except PreconditionFailedError:
                continue
        raise RuntimeError(f"EvalRun CAS exhausted retries for {rid}")  # pragma: no cover


def _with_index(run: EvalRun, field_name: str, batch_index: int) -> EvalRun | None:
    current: list[int] = getattr(run, field_name)
    if batch_index in current:
        return None  # idempotent — redelivery no-op
    return msgspec.structs.replace(run, **{field_name: [*current, batch_index]})
