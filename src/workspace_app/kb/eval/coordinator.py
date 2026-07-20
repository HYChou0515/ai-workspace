"""EvalCoordinator — the retrieval-eval fan-out engine (#535).

Consumes one ``EvalJob`` JobType and dispatches on ``payload.kind``, mirroring
the #227 index fan-out:

- ``dispatch`` — the cronjob's single job: list every collection, enqueue one
  ``split`` each.
- ``split`` — for one collection: list chunk IDs (metas-only, no vectors — cf.
  #508), deterministically sample, batch, seed the ``EvalRun`` CAS join, enqueue
  a ``batch`` per batch (``partition_key=None`` → parallel across the GPU fleet).
- ``batch`` — score its sampled chunks (make question → retrieve → rank), stage
  the ranks, record itself in the join, and — if it closes the set — win the
  finalize gate and enqueue ``finalize``.
- ``finalize`` — rejoin every batch's staged ranks, aggregate into recall@k /
  MRR, write the durable ``EvalResult``, and clear the staging rows.

The LLM (question generation) and the Retriever are injected seams — tests pass a
fake ``ILlm`` + a fake search. Consumption machinery mirrors the sanity / index
coordinators (``start_consuming`` / ``aclose``).
"""

from __future__ import annotations

import asyncio
import logging

from specstar import QB, Schema, SpecStar
from specstar.types import ResourceIDNotFoundError, TaskStatus

from ...resources import Collection, DocChunk, EvalResult
from ...resources.eval import eval_run_id
from ..llm import ILlm
from ..retriever import Retriever
from .batch import BatchResult, aggregate, score_batch
from .jobs import EvalJob, EvalJobPayload
from .run_store import EvalRunStore
from .sample import into_batches, select_sample

_LOGGER = logging.getLogger(__name__)
_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02


class EvalCoordinator:
    def __init__(
        self,
        spec: SpecStar,
        llm: ILlm,
        *,
        retriever: Retriever | None = None,
        sample_size: int = 300,
        batch_size: int = 25,
        depth: int = 20,
        ks: tuple[int, ...] = (1, 3, 5, 10),
        message_queue_factory: object | None = None,
    ) -> None:
        # ``retriever`` is injected post-construction (``set_retriever``) because
        # it is built after ``build_coordinators`` — same as the agentic
        # card-drafter. The producer side (enqueue + model registration) needs
        # only ``spec``; the consumer side (``batch``) needs the retriever, and
        # the worker/all-in-one wires it before it starts consuming.
        self._spec = spec
        self._llm = llm
        self._retriever = retriever
        self._sample_size = sample_size
        self._batch_size = batch_size
        self._depth = depth
        self._ks = ks
        self._runs = EvalRunStore(spec)
        self._chunk_rm = spec.get_resource_manager(DocChunk)
        self._collection_rm = spec.get_resource_manager(Collection)
        self._result_rm = spec.get_resource_manager(EvalResult)

        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(EvalJob, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(EvalJob)
        self._consuming = False

    def set_retriever(self, retriever: Retriever) -> None:
        """Inject the retriever post-construction (built after the coordinator).
        Must be called before this coordinator starts consuming ``batch`` jobs."""
        self._retriever = retriever

    # ── producer (the cronjob / route enqueues this one job) ─────────
    def enqueue_dispatch(self, run_label: str, *, seed: str = "", sample_size: int = 0) -> None:
        """Kick off a whole run: one ``dispatch`` job that fans out to every
        collection. The nightly cronjob POSTs this (or the auto route)."""
        self._job_rm.create(
            EvalJob(
                payload=EvalJobPayload(
                    kind="dispatch", run_label=run_label, seed=seed, sample_size=sample_size
                )
            )
        )

    # ── consume ──────────────────────────────────────────────────────
    def _handle(self, job) -> None:  # job: Resource[EvalJob]
        payload = job.data.payload
        assert isinstance(payload, EvalJobPayload)
        if payload.kind == "dispatch":
            self._dispatch(payload)
        elif payload.kind == "split":
            self._split(payload)
        elif payload.kind == "batch":
            self._batch(payload)
        elif payload.kind == "finalize":
            self._finalize(payload)
        else:  # pragma: no cover — defensive
            _LOGGER.warning("eval: unknown job kind %r", payload.kind)

    def _dispatch(self, payload: EvalJobPayload) -> None:
        for cid in self._all_collection_ids():
            self._job_rm.create(
                EvalJob(
                    payload=EvalJobPayload(
                        kind="split",
                        run_label=payload.run_label,
                        collection_id=cid,
                        seed=payload.seed,
                        sample_size=payload.sample_size,
                    ),
                    partition_key=cid,
                )
            )

    def _split(self, payload: EvalJobPayload) -> None:
        cid = payload.collection_id
        seed = payload.seed or payload.run_label
        sample_size = payload.sample_size or self._sample_size
        sample = select_sample(self._chunk_ids(cid), seed, sample_size)
        batches = into_batches(sample, self._batch_size)
        self._runs.start(cid, payload.run_label, len(batches), seed=seed, sample_size=sample_size)
        for i, chunk_ids in enumerate(batches):
            self._job_rm.create(
                EvalJob(
                    payload=EvalJobPayload(
                        kind="batch",
                        run_label=payload.run_label,
                        collection_id=cid,
                        batch_index=i,
                        chunk_ids=chunk_ids,
                    ),
                    partition_key=None,
                )
            )
        if not batches:  # empty collection — no batch will trigger finalize
            self._enqueue_finalize(cid, payload.run_label)

    def _batch(self, payload: EvalJobPayload) -> None:
        cid = payload.collection_id
        chunks = self._load_chunks(payload.chunk_ids)
        result = score_batch(self._llm, self._search, [cid], chunks)
        self._runs.stage_batch(
            cid,
            payload.run_label,
            payload.batch_index,
            chunk_ranks=result.chunk_ranks,
            doc_ranks=result.doc_ranks,
            n_kept=result.n_kept,
            n_dropped=result.n_dropped,
        )
        self._runs.mark_done(cid, payload.run_label, payload.batch_index)
        if self._runs.claim_finalize(cid, payload.run_label):
            self._enqueue_finalize(cid, payload.run_label)

    def _finalize(self, payload: EvalJobPayload) -> None:
        cid = payload.collection_id
        run = self._runs.get(cid, payload.run_label)
        if run is None or run.status != "running":
            return  # idempotent — already finalized or vanished
        results = [
            BatchResult(
                chunk_ranks=b.chunk_ranks,
                doc_ranks=b.doc_ranks,
                n_kept=b.n_kept,
                n_dropped=b.n_dropped,
            )
            for b in self._runs.read_batches(cid, payload.run_label)
        ]
        agg = aggregate(results, self._ks)
        self._result_rm.create_or_update(
            eval_run_id(cid, payload.run_label),
            EvalResult(
                collection_id=cid,
                run_label=payload.run_label,
                seed=run.seed,
                sample_size=run.sample_size,
                n_generated=agg.n_kept + agg.n_dropped,
                n_kept=agg.n_kept,
                n_dropped=agg.n_dropped,
                recall_chunk=agg.recall_chunk,
                mrr_chunk=agg.mrr_chunk,
                recall_doc=agg.recall_doc,
                mrr_doc=agg.mrr_doc,
            ),
        )
        self._runs.clear_batches(cid, payload.run_label)
        self._runs.finish(cid, payload.run_label, status="done")
        _LOGGER.info(
            "eval: finalized collection=%s run=%s recall@1(chunk)=%.3f n_kept=%d",
            cid,
            payload.run_label,
            agg.recall_chunk.get("1", 0.0),
            agg.n_kept,
        )

    # ── helpers ──────────────────────────────────────────────────────
    def _enqueue_finalize(self, collection_id: str, run_label: str) -> None:
        self._job_rm.create(
            EvalJob(
                payload=EvalJobPayload(
                    kind="finalize", run_label=run_label, collection_id=collection_id
                ),
                partition_key=collection_id,
            )
        )

    def _all_collection_ids(self) -> list[str]:
        return [m.resource_id for m in self._collection_rm.search_resources(QB.all())]  # ty: ignore[invalid-argument-type]

    def _chunk_ids(self, collection_id: str) -> list[str]:
        """Metas-only — never loads chunk text/vectors (cf. #508)."""
        query = (QB["collection_id"] == collection_id).build()
        return [m.resource_id for m in self._chunk_rm.search_resources(query)]

    def _load_chunks(self, chunk_ids: list[str]) -> list[tuple[str, str, str]]:
        """(chunk_id, source_doc_id, text) for each sampled chunk; a chunk deleted
        between split and batch is skipped."""
        out: list[tuple[str, str, str]] = []
        for cid in chunk_ids:
            try:
                data = self._chunk_rm.get(cid).data
            except ResourceIDNotFoundError:
                continue
            assert isinstance(data, DocChunk)
            out.append((cid, data.source_doc_id, data.text))
        return out

    def _search(self, query: str, collection_ids: list[str]):
        assert self._retriever is not None, "retriever not wired (call set_retriever)"
        return self._retriever.search(query, collection_ids, depth=self._depth)

    # ── consumption machinery (mirrors sanity / index) ───────────────
    @property
    def consuming(self) -> bool:
        return self._consuming

    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        self._ensure_consuming()

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    def _stop_consuming(self) -> None:
        import contextlib

        with contextlib.suppress(RuntimeError):
            self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]
        self._consuming = False

    async def aclose(self) -> None:
        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
