"""CardGenCoordinator (#175) — the background runner for "自動 context card".

A user picks documents in a collection's Context Cards tab and asks the system
to draft glossary cards from them. That work is an LLM pass over each document,
so it runs OFF the request on a specstar job queue (the same durable, cross-pod
machinery as wiki maintenance / indexing), and its proposals land on the job's
``artifact`` for a human to review before committing.

  - **enqueue** (request-side, fast): create one ``CardGenJob`` per run,
    ``partition_key`` = the collection id so a collection's generation runs
    serialise across consumers.
  - **handle** (consumer-side): read each selected document's extracted text,
    call the ``CardDrafter`` for its draft cards, dedup them by normalised key
    (``merge_drafts``), classify each against the collection's existing cards
    (new / update / skip-duplicate), and write the survivors onto the job's
    ``artifact`` (``ctx.set_artifact``) — which specstar persists on COMPLETED.

Status + proposals are read straight off the job resource (``GET`` it): the job
IS the durable state, so there is no separate state row to keep coherent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from specstar import QB, Schema, SpecStar
from specstar.types import ResourceIDNotFoundError, RevisionStatus, TaskStatus

from ..resources import ContextCard, SourceDoc
from .card_gen import (
    CardDraft,
    CardDrafter,
    CardGenArtifact,
    CardGenJob,
    CardGenPayload,
    CommitResult,
    ProposedCard,
    classify_against_existing,
    merge_drafts,
)
from .context_cards import cards_with_ids_for_collections, derive_norm_keys
from .job_audit import preserve_job_creator
from .wiki.sources import SpecstarWikiSources

_LOGGER = logging.getLogger(__name__)

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02  # aclose() poll cadence while waiting for the queue to drain


def _exists(rm, resource_id: str) -> bool:
    """Whether a resource id still resolves — used at commit to tell an in-place
    card update from a fall-back create when the target was deleted."""
    try:
        rm.get(resource_id)
    except ResourceIDNotFoundError:
        return False
    return True


class CardGenCoordinator:
    """Per-collection context-card generation scheduler, backed by a specstar
    job queue (cross-pod serial via ``partition_key``)."""

    def __init__(
        self,
        spec: SpecStar,
        drafter: CardDrafter,
        *,
        message_queue_factory: object | None = None,
        get_user_id: Callable[[], str] | None = None,
    ) -> None:
        self._spec = spec
        self._drafter = drafter
        # Who an enqueue is credited to — the real user in a request; production
        # injects the same get_user_id the other coordinators use.
        self._get_user_id = get_user_id or (lambda: spec.get_resource_manager(SourceDoc).user)
        # The job model's handler needs runtime deps (the drafter), so it can't
        # be registered in make_spec — register it here, with the queue backend
        # set PER-MODEL (a global configure() doesn't reach a real pg/disk
        # backend). Default = the specstar-backed Simple queue (multipod).
        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(CardGenJob, "v1"),
            # handler takes (resource, job_context) — specstar inspects the
            # signature for that name; ty types job_handler as single-arg.
            job_handler=self._handle,  # ty: ignore[invalid-argument-type]
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(CardGenJob)
        # Preserve each generation job's creator across its lifecycle instead of
        # stamping the worker default; producers below set the user via using().
        preserve_job_creator(self._job_rm)
        self._consuming = False

    # ── enqueue (producer) ───────────────────────────────────────────
    def enqueue(
        self, collection_id: str, doc_ids: list[str], *, requested_by: str | None = None
    ) -> str:
        """Queue a generation run over ``doc_ids`` for ``collection_id`` and
        return the job id (the FE polls it for status + proposals). Returns
        immediately — the drafting runs in the background consumer. ``requested_by``
        credits the job (a route leaves it ``None`` → the current request user)."""
        actor = requested_by if requested_by is not None else self._get_user_id()
        with self._job_rm.using(user=actor):
            rev = self._job_rm.create(
                CardGenJob(
                    payload=CardGenPayload(collection_id=collection_id, doc_ids=list(doc_ids)),
                    partition_key=collection_id,
                )
            )
        return rev.resource_id

    # ── status / proposals (read) ────────────────────────────────────
    def _job(self, job_id: str) -> CardGenJob:
        data = self._job_rm.get(job_id).data
        assert isinstance(data, CardGenJob)  # the CardGenJob manager yields a CardGenJob (ty)
        return data

    def status(self, job_id: str) -> TaskStatus:
        """The run's current status (PENDING / PROCESSING / COMPLETED / FAILED)."""
        return self._job(job_id).status

    def proposals(self, job_id: str) -> CardGenArtifact:
        """The run's reviewable proposals (empty until it COMPLETED)."""
        art = self._job(job_id).artifact
        return art if isinstance(art, CardGenArtifact) else CardGenArtifact()

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    # ── review state (persisted on the artifact, #175 Q7) ────────────
    def save_review(
        self, job_id: str, proposals: list[ProposedCard], *, reviewer: str | None = None
    ) -> None:
        """Persist the reviewer's edited / decided proposals back onto the job
        artifact, so leaving the review page and returning restores progress
        (the job is the durable store — no separate review row). Replaces the
        artifact's proposals wholesale with what the FE sends back."""
        res = self._job_rm.get(job_id)
        job = res.data
        assert isinstance(job, CardGenJob)
        job.artifact = CardGenArtifact(proposals=list(proposals))
        # A COMPLETED job's revision isn't a draft, so a plain modify is refused;
        # write the review state as a draft (keeps review churn out of history,
        # same as the wiki build-state upsert). job.status stays COMPLETED.
        with self._job_rm.using(user=reviewer or self._get_user_id()):
            self._job_rm.modify(job_id, job, status=RevisionStatus.draft)

    def commit(self, job_id: str, *, committed_by: str | None = None) -> CommitResult:
        """Write the run's ACCEPTED proposals to real ``ContextCard``s (#106's
        author/edit semantics): ``new`` → create, ``update`` → overwrite the
        target card (re-deriving ``norm_keys``); a target deleted since
        generation falls back to a create. Proposals the reviewer didn't accept —
        and any with no usable key — are skipped. Returns the tallies."""
        job = self._job(job_id)
        cid = job.payload.collection_id
        art = job.artifact if isinstance(job.artifact, CardGenArtifact) else CardGenArtifact()
        cardrm = self._spec.get_resource_manager(ContextCard)
        result = CommitResult()
        with cardrm.using(user=committed_by or self._get_user_id()):
            for p in art.proposals:
                if p.decision != "accepted" or not derive_norm_keys(p.keys):
                    result.skipped += 1
                    continue
                card = ContextCard(
                    collection_id=cid,
                    keys=p.keys,
                    norm_keys=derive_norm_keys(p.keys),
                    title=p.title,
                    body=p.body,
                )
                if p.mode == "update" and p.target_card_id and _exists(cardrm, p.target_card_id):
                    # New COMMITTED revision of the target (create_or_update, not
                    # modify — modify refuses a committed→committed overwrite).
                    cardrm.create_or_update(p.target_card_id, card)
                    result.updated += 1
                else:
                    # New card — or the update target vanished since generation.
                    cardrm.create(card)
                    result.created += 1
        return result

    # ── consume (handler — runs in the queue's consumer thread) ──────
    def _handle(self, resource, job_context) -> None:  # Resource[CardGenJob], JobContext
        """Draft + dedup + classify one generation run, OFF the main loop. The
        drafter is synchronous (``ILlm.collect``), so no event loop is spun up.
        Proposals are written onto the job artifact; returning normally →
        COMPLETED (specstar persists the artifact). specstar calls this with the
        job resource positionally + the ``job_context`` keyword (it inspects the
        signature for that name)."""
        payload = job_context.payload
        assert isinstance(payload, CardGenPayload)
        cid = payload.collection_id
        sources = SpecstarWikiSources(self._spec, cid)
        raw: list[tuple[str, str, CardDraft]] = []
        for doc_id in payload.doc_ids:
            ref = sources.ref_by_id(doc_id)
            if ref is None:
                continue  # doc deleted between enqueue and run — skip it
            for draft in self._drafter.draft(doc_path=ref.path, doc_text=ref.text):
                raw.append((doc_id, ref.path, draft))
        proposals = merge_drafts(raw)
        existing = cards_with_ids_for_collections(self._spec, [cid])
        kept = [p for p in proposals if classify_against_existing(p, existing) != "skip"]
        job_context.set_artifact(CardGenArtifact(proposals=kept))

    # ── lifecycle ────────────────────────────────────────────────────
    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        """Start this process's background consumer once (idempotent).
        create_app calls it at startup so idle pods help drain the queue."""
        self._ensure_consuming()

    @property
    def consuming(self) -> bool:
        """Whether the background consumer is running (#312) — observable so the
        API's ``run_consumers`` gate can be asserted and a worker can report it
        is draining its JobType."""
        return self._consuming

    def _stop_consuming(self) -> None:
        self._consuming = False
        self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]

    async def aclose(self) -> None:
        """Await all in-flight generation (graceful shutdown / test sync) by
        polling until the queue drains. Starts the consumer first so a
        coordinator that never called ``start_consuming`` still flushes; stops it
        once drained so we don't leak idle daemon threads."""
        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
