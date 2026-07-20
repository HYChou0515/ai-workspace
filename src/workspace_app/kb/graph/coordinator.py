"""GraphCoordinator — the metric-extraction fan-out engine (#534).

Consumes one ``GraphJob`` JobType and dispatches on ``payload.kind``:

- ``dispatch`` — the cronjob's single job: list the OPTED-IN collections
  (``Collection.use_graph``) and enqueue one ``split`` each.
- ``split`` — for one collection: find its distinct docs (from the chunks'
  ``source_doc_id``), batch them, enqueue a ``batch`` per batch
  (``partition_key=None`` → parallel across the GPU fleet).
- ``batch`` — for each doc in the batch, load the doc's chunk texts and
  ``write_doc_claims`` (extract → idempotent wipe+rewrite).

No finalize / CAS join: per-doc writes are independent and idempotent. The LLM
(extraction) is an injected seam — tests pass a fake ``ILlm``. Consumption
machinery mirrors the sanity / index / eval coordinators.
"""

from __future__ import annotations

import asyncio
import logging

from specstar import QB, Count, Schema, SpecStar
from specstar.types import TaskStatus

from ...perm import Permission
from ...resources import Collection, DocChunk
from ...resources.kb import SourceDoc
from ..doc_permission import (
    collection_claim_mirror,
    push_doc_override_to_claims,
    push_mirror_to_claims,
    reset_doc_override_on_claims,
)
from ..eval.sample import into_batches
from ..llm import ILlm
from .jobs import GraphJob, GraphJobPayload
from .write import write_doc_claims

_LOGGER = logging.getLogger(__name__)
_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02


class GraphCoordinator:
    def __init__(
        self,
        spec: SpecStar,
        llm: ILlm,
        *,
        batch_size: int = 20,
        message_queue_factory: object | None = None,
    ) -> None:
        self._spec = spec
        self._llm = llm
        self._batch_size = batch_size
        self._collection_rm = spec.get_resource_manager(Collection)
        self._chunk_rm = spec.get_resource_manager(DocChunk)
        self._doc_rm = spec.get_resource_manager(SourceDoc)

        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(GraphJob, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(GraphJob)
        self._consuming = False

    # ── producer (cronjob / route enqueues this one job) ─────────────
    def enqueue_dispatch(self) -> None:
        """Kick off a whole extraction pass over every opted-in collection."""
        self._job_rm.create(GraphJob(payload=GraphJobPayload(kind="dispatch")))

    # ── consume ──────────────────────────────────────────────────────
    def _handle(self, job) -> None:  # job: Resource[GraphJob]
        payload = job.data.payload
        assert isinstance(payload, GraphJobPayload)
        if payload.kind == "dispatch":
            self._dispatch()
        elif payload.kind == "split":
            self._split(payload)
        elif payload.kind == "batch":
            self._batch(payload)
        else:  # pragma: no cover — defensive
            _LOGGER.warning("graph: unknown job kind %r", payload.kind)

    def _dispatch(self) -> None:
        for cid in self._opted_in_collection_ids():
            self._job_rm.create(
                GraphJob(
                    payload=GraphJobPayload(kind="split", collection_id=cid),
                    partition_key=cid,
                )
            )

    def reconcile_mirrors(self, collection_id: str) -> None:
        """#534 slice 2 — bring every claim in the collection back onto the CURRENT
        read permission of the deck it came from, before extracting anything new.

        This is where the backfill lives. Claims written before the mirror existed
        carry no verdict at all, and the scope reads "never written" as invisible,
        so they need a real write — ``rm.migrate`` cannot help, since it only
        re-extracts indexed values from data that already holds them. Putting it at
        the head of the job that already runs weekly means the backfill needs no
        operator step and any later drift heals itself, rather than lingering until
        someone notices.

        The overridden decks are identified FIRST and then excluded from the
        "no override" push, rather than being reset and re-tightened afterwards.
        Resetting first would publish every tightened deck's numbers for the length
        of the re-tightening loop — each push commits separately, so that window is
        real — and would leave them published until the next run if the loop
        stopped partway. Overrides are rare, so this is typically two bulk patches
        for the whole collection, and a patch that changes nothing writes no
        revision, so the steady-state cost is a read.
        """
        mirror = collection_claim_mirror(self._spec, collection_id)
        owner = mirror["collection_created_by"]
        push_mirror_to_claims(
            self._spec,
            collection_id,
            visibility=mirror["collection_visibility"],
            read_meta=mirror["collection_read_meta"],
            read_content=mirror["collection_read_content"],
            created_by=owner,
        )
        overridden = self._overridden_docs(collection_id)
        for doc_id, override in overridden:
            push_doc_override_to_claims(
                self._spec,
                doc_id,
                visibility=override.visibility,
                read_meta=list(override.read_meta),
                read_content=list(override.read_content),
                created_by=owner,
            )
        reset_doc_override_on_claims(
            self._spec,
            collection_id,
            created_by=owner,
            except_docs=[doc_id for doc_id, _ in overridden],
        )

    def _overridden_docs(self, collection_id: str) -> list[tuple[str, Permission]]:
        """The decks in the collection carrying their OWN read override (#308).
        Queried on the indexed ``permission.visibility``, which is only ever
        written fresh, so an un-overridden deck is never in the candidate set."""
        q = QB["collection_id"] == collection_id
        q = q & QB["permission.visibility"].is_not_null()
        out: list[tuple[str, Permission]] = []
        for r in self._doc_rm.list_resources(q.build()):
            doc = r.data
            assert isinstance(doc, SourceDoc)
            if doc.permission is not None:
                out.append((r.info.resource_id, doc.permission))  # ty: ignore[unresolved-attribute]
        return out

    def _split(self, payload: GraphJobPayload) -> None:
        cid = payload.collection_id
        self.reconcile_mirrors(cid)
        for batch in into_batches(self._collection_doc_ids(cid), self._batch_size):
            self._job_rm.create(
                GraphJob(
                    payload=GraphJobPayload(kind="batch", collection_id=cid, doc_ids=batch),
                    partition_key=None,
                )
            )

    def _batch(self, payload: GraphJobPayload) -> None:
        cid = payload.collection_id
        for doc_id in payload.doc_ids:
            write_doc_claims(
                self._spec,
                self._llm,
                collection_id=cid,
                source_doc_id=doc_id,
                chunks=self._doc_chunks(doc_id),
            )

    # ── helpers ──────────────────────────────────────────────────────
    def _opted_in_collection_ids(self) -> list[str]:
        return [
            m.resource_id
            for m in self._collection_rm.search_resources(QB["use_graph"].eq(True).build())
        ]

    def _collection_doc_ids(self, collection_id: str) -> list[str]:
        """Distinct docs with chunks in the collection — a native group-by on the
        chunks' ``source_doc_id`` (no per-chunk load)."""
        rows = self._chunk_rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
            QB["source_doc_id"],
            {"n": Count()},
            query=(QB["collection_id"] == collection_id).build(),
        )
        return [r.key for r in rows if r.key]

    def _doc_chunks(self, doc_id: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for r in self._chunk_rm.list_resources((QB["source_doc_id"] == doc_id).build()):
            data = r.data
            assert isinstance(data, DocChunk)
            out.append((r.info.resource_id, data.text))  # ty: ignore[unresolved-attribute]
        return out

    # ── consumption machinery (mirrors sanity / index / eval) ────────
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
