"""CardGenCoordinator (#175, fanned out in #414) — the background runner for
"自動 context card".

A user picks documents in a collection's Context Cards tab and asks the system
to draft glossary cards from them. That work is an LLM pass over each document,
so it runs OFF the request on a specstar job queue (the same durable, cross-pod
machinery as wiki maintenance / indexing), and its proposals land on the run for
a human to review before committing.

#414 fans the per-document LLM passes out so they parallelise across worker pods,
porting the #227 index fan-out (a specstar consumer runs ONE job at a time per
pod, so parallelism can only come from many pods each pulling a different job):

  - **enqueue** (request-side, fast): seed a :class:`CardGenRun` (its id is what
    the FE polls) and create ONE ``split`` ``CardGenJob`` (``partition_key`` = the
    collection id so a collection's runs serialise across consumers).
  - **split** (consumer-side): mark the run running, then fan it out into one
    ``process`` job per document (``partition_key=None`` → free cross-pod
    parallelism). A run over ≤1 document short-circuits and runs inline (no
    fan-out overhead), mirroring the index coordinator's ``_index_whole``.
  - **process** (consumer-side, parallel): digest ONE document, stage its
    :class:`CardGenUnit`, and record it done — or, if the drafter gives up on it,
    record it failed so one bad document can't wedge the run (#414 partial
    tolerance, #249's philosophy). Whoever accounts for the last document wins the
    CAS finalize gate and enqueues the ``finalize`` job.
  - **finalize** (consumer-side, exactly once, serialised per collection): read
    the staged digests in document order, dedup them by normalised key
    (``merge_drafts``), classify each against the collection's existing cards
    (new / update / skip-duplicate), write the survivors onto the run, and raise
    the clarification questions single-threaded (so the collection-level term
    dedup stays race-free — ``open_or_merge_term_question`` is not CAS-safe).

Status + proposals are read straight off the run (``CardGenRun``): the run IS the
durable FE-facing state, so there is no separate state row to keep coherent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from specstar import QB, Schema, SpecStar
from specstar.types import ResourceIDNotFoundError, TaskStatus

from ..resources import ContextCard, SourceDoc
from .card_gen import (
    CardDraft,
    CardDrafter,
    CardGenArtifact,
    CardGenJob,
    CardGenPayload,
    CardGenRunSummary,
    CardGenUnit,
    CommitResult,
    DocDigest,
    ProposedCard,
    classify_against_existing,
    merge_drafts,
)
from .card_gen_run import CardGenRunStore
from .card_gen_sources import CardGenSources
from .context_cards import cards_with_ids_for_collections, derive_norm_keys
from .doc_questions import (
    add_description_question,
    open_or_merge_term_question,
    plan_doc_questions,
)
from .job_audit import preserve_job_creator

_LOGGER = logging.getLogger(__name__)

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02  # aclose() poll cadence while waiting for the queue to drain

# The run's own lifecycle string → the TaskStatus the FE polls (#414: status lives
# on the run, not on any one fanned-out job).
_RUN_STATUS = {
    "pending": TaskStatus.PENDING,
    "running": TaskStatus.PROCESSING,
    "done": TaskStatus.COMPLETED,
    "error": TaskStatus.FAILED,
    # #415 review-resolution terminals — both are past COMPLETED to the poller.
    "committed": TaskStatus.COMPLETED,
    "dismissed": TaskStatus.COMPLETED,
}


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
    job queue (the #227 fan-out: one ``process`` job per document, cross-pod
    parallel; a CAS-joined :class:`CardGenRun` is the durable FE-facing state)."""

    def __init__(
        self,
        spec: SpecStar,
        drafter: CardDrafter,
        *,
        message_queue_factory: object | None = None,
        get_user_id: Callable[[], str] | None = None,
        max_questions_per_doc: int = 5,
    ) -> None:
        self._spec = spec
        self._drafter = drafter
        self._runs = CardGenRunStore(spec)
        # #377 guardrail ③: cap the clarification questions one document may raise
        # so a pathological digest can't flood the inbox (terms fill the budget
        # first, then descriptions).
        self._max_questions_per_doc = max_questions_per_doc
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
            # handler takes the job resource positionally (single-arg, like the
            # index coordinator); specstar adapts to the signature.
            job_handler=self._handle,
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
        return the RUN id (the FE polls it for status + proposals). Returns
        immediately — the drafting fans out to background consumers. ``requested_by``
        credits the job (a route leaves it ``None`` → the current request user)."""
        actor = requested_by if requested_by is not None else self._get_user_id()
        run_id = self._runs.start(collection_id, doc_ids)
        with self._job_rm.using(user=actor):
            self._job_rm.create(
                CardGenJob(
                    payload=CardGenPayload(
                        collection_id=collection_id,
                        doc_ids=list(doc_ids),
                        kind="split",
                        run_id=run_id,
                    ),
                    partition_key=collection_id,
                )
            )
        return run_id

    # ── status / proposals (read off the run) ────────────────────────
    def status(self, run_id: str) -> TaskStatus:
        """The run's current status (PENDING / PROCESSING / COMPLETED / FAILED),
        derived from the durable :class:`CardGenRun` lifecycle."""
        run = self._runs.get(run_id)
        return _RUN_STATUS[run.status] if run is not None else TaskStatus.PENDING

    def proposals(self, run_id: str) -> CardGenArtifact:
        """The run's reviewable proposals (empty until it finalizes)."""
        run = self._runs.get(run_id)
        if run is None:
            return CardGenArtifact()
        return CardGenArtifact(proposals=list(run.proposals))

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    # ── review state (persisted on the run, #175 Q7) ─────────────────
    def save_review(
        self, run_id: str, proposals: list[ProposedCard], *, reviewer: str | None = None
    ) -> None:
        """Persist the reviewer's edited / decided proposals back onto the run, so
        leaving the review page and returning restores progress (the run is the
        durable store — no separate review row). Replaces the run's proposals
        wholesale with what the FE sends back."""
        self._runs.set_proposals(run_id, proposals)

    def commit(self, run_id: str, *, committed_by: str | None = None) -> CommitResult:
        """Write the run's ACCEPTED proposals to real ``ContextCard``s (#106's
        author/edit semantics): ``new`` → create, ``update`` → overwrite the
        target card (re-deriving ``norm_keys``); a target deleted since
        generation falls back to a create. Proposals the reviewer didn't accept —
        and any with no usable key — are skipped. Returns the tallies."""
        run = self._runs.get(run_id)
        if run is None or run.status != "done":
            return CommitResult()  # gone, or already reviewed — no double card-write
        cid = run.collection_id
        cardrm = self._spec.get_resource_manager(ContextCard)
        result = CommitResult()
        with cardrm.using(user=committed_by or self._get_user_id()):
            for p in run.proposals:
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
        # #415: the run is reviewed — resolve it out of the 待審核 queue.
        self._runs.mark_committed(run_id)
        return result

    def dismiss(self, run_id: str) -> None:
        """#415: discard a run's proposals without writing any card — it leaves the
        待審核 queue (status ``dismissed``)."""
        self._runs.mark_dismissed(run_id)

    def pending_runs(self, collection_id: str) -> list[CardGenRunSummary]:
        """The collection's finalized-but-unreviewed runs — the 待審核 queue rows
        (#415), newest first. The FE lazy-loads each run's proposals on expand."""
        return [
            CardGenRunSummary(
                run_id=rid, collection_id=collection_id, proposal_count=len(run.proposals)
            )
            for rid, run in self._runs.pending_for_collection(collection_id)
        ]

    # ── consume (handler — runs in the queue's consumer thread) ──────
    def _handle(self, job) -> None:  # job: Resource[CardGenJob]
        """Dispatch one generation step by ``kind`` (#414), OFF the main loop. The
        drafter is synchronous (``ILlm.collect``), so no event loop is spun up.
        specstar calls this with the job resource positionally."""
        payload = job.data.payload
        assert isinstance(payload, CardGenPayload)
        requester = job.info.created_by
        if payload.kind == "process":
            self._handle_process(payload, requester)
        elif payload.kind == "finalize":
            self._finalize(payload.run_id)
        else:
            self._handle_split(payload, requester)

    def _handle_split(self, payload: CardGenPayload, requester: str) -> None:
        """Plan the run: mark it running, then fan out one ``process`` job per
        document — or, for ≤1 document, digest inline and finalize with no fan-out
        overhead (mirrors the index coordinator's ``_index_whole``)."""
        run_id, cid, doc_ids = payload.run_id, payload.collection_id, payload.doc_ids
        self._runs.begin(run_id)
        if len(doc_ids) <= 1:
            sources = CardGenSources(self._spec, cid)
            for doc_index, doc_id in enumerate(doc_ids):
                self._process_one(run_id, doc_index, doc_id, sources)
            self._finalize(run_id)
            return
        # #414: credit the fan-out jobs to the requester (not the bare worker
        # default) so the chain reading job.created_by stays the real user.
        with self._job_rm.using(user=requester):
            for doc_index in range(len(doc_ids)):
                self._job_rm.create(
                    CardGenJob(
                        payload=CardGenPayload(
                            collection_id=cid,
                            kind="process",
                            run_id=run_id,
                            doc_index=doc_index,
                        ),
                        # No partition_key (#414): process jobs parallelise across
                        # pods; the CAS join, not the queue, guards correctness.
                        partition_key=None,
                    )
                )

    def _handle_process(self, payload: CardGenPayload, requester: str) -> None:
        """Digest ONE document end-to-end, stage it, record it done (or failed),
        and — if it accounts for the last document — win the finalize gate and
        enqueue the finalize job. The doc id is read from the run's ordered doc set
        so the process payload stays a cheap ``(run_id, doc_index)`` reference."""
        run = self._runs.get(payload.run_id)
        if run is None:
            return  # run cascaded away (collection deleted between split and run)
        doc_id = run.doc_ids[payload.doc_index]
        sources = CardGenSources(self._spec, payload.collection_id)
        self._process_one(payload.run_id, payload.doc_index, doc_id, sources)
        if self._runs.claim_finalize(payload.run_id):
            self._enqueue_finalize(payload.run_id, payload.collection_id, requester)

    def _process_one(
        self, run_id: str, doc_index: int, doc_id: str, sources: CardGenSources
    ) -> None:
        """Digest one document and record its outcome on the run. A deleted doc is
        digested to nothing (``done``, not a failure — matches the pre-fan-out
        skip). A drafter that raises (post-failover, so a genuine give-up) marks
        the doc ``failed`` so one bad document can't wedge the whole run — the
        finalize gate still closes (#414 partial tolerance)."""
        ref = sources.ref_by_id(doc_id)
        if ref is None:
            self._runs.mark_done(run_id, doc_index)  # doc deleted before run — nothing to digest
            return
        try:
            digest = self._drafter.digest(doc_path=ref.path, doc_text=ref.text)
        except Exception:  # noqa: BLE001 — one doc's give-up must not sink the run
            _LOGGER.exception("CardGen: digest failed for doc %s (run %s)", doc_id, run_id)
            self._runs.mark_failed(run_id, doc_index)
            return
        self._stage_unit(run_id, doc_index, doc_id, ref.path, digest)
        self._runs.mark_done(run_id, doc_index)

    def _enqueue_finalize(self, run_id: str, collection_id: str, requester: str) -> None:
        """Enqueue the exactly-once finalize step, serialised per collection
        (``partition_key`` = the collection id) so the single-threaded, non-CAS
        question dedup can't race a concurrent run's finalize on the same
        collection (an upload burst auto-triggers one run per doc). Credited to the
        run's requester (preserved across the fan-out chain)."""
        with self._job_rm.using(user=requester):
            self._job_rm.create(
                CardGenJob(
                    payload=CardGenPayload(
                        collection_id=collection_id, kind="finalize", run_id=run_id
                    ),
                    partition_key=collection_id,
                )
            )

    def _finalize(self, run_id: str) -> None:
        """Exactly-once close-out of a run: read the staged digests in document
        order, merge + classify their drafts into the run's proposals, raise their
        clarification questions single-threaded, drop the staging, and stamp the
        terminal status. A run whose every document failed ends ``error``;
        otherwise ``done`` (possibly with partial proposals)."""
        run = self._runs.get(run_id)
        if run is None:
            return  # run cascaded away mid-flight
        cid = run.collection_id
        units = self._staged_units(run_id)
        raw: list[tuple[str, str, CardDraft]] = []
        per_doc: list[tuple[str, DocDigest]] = []  # #377: questions to raise, per doc
        for unit in units:
            for draft in unit.digest.cards:
                raw.append((unit.doc_id, unit.path, draft))
            per_doc.append((unit.doc_id, unit.digest))
        proposals = merge_drafts(raw)
        existing = cards_with_ids_for_collections(self._spec, [cid])
        kept = [p for p in proposals if classify_against_existing(p, existing) != "skip"]
        self._runs.set_proposals(run_id, kept)
        self._raise_questions(cid, per_doc, existing)
        self._clear_staged(run_id)
        # All documents failed (none digested) → the run failed; else it produced
        # proposals (possibly partial, if some docs failed) → done.
        all_failed = run.total > 0 and len(run.done) == 0
        self._runs.finish(run_id, status="error" if all_failed else "done")

    # ── fan-out staging (per-doc digests, #414) ──────────────────────
    def _stage_unit(
        self, run_id: str, doc_index: int, doc_id: str, path: str, digest: DocDigest
    ) -> None:
        rm = self._spec.get_resource_manager(CardGenUnit)
        rm.create_or_update(
            f"{run_id}.u{doc_index}",
            CardGenUnit(
                run_id=run_id, doc_index=doc_index, doc_id=doc_id, path=path, digest=digest
            ),
        )

    def _staged_units(self, run_id: str) -> list[CardGenUnit]:
        rm = self._spec.get_resource_manager(CardGenUnit)
        rows = [r.data for r in rm.list_resources((QB["run_id"] == run_id).build())]
        units = [u for u in rows if isinstance(u, CardGenUnit)]
        return sorted(units, key=lambda u: u.doc_index)  # doc order → deterministic merge

    def _clear_staged(self, run_id: str) -> None:
        rm = self._spec.get_resource_manager(CardGenUnit)
        for u in self._staged_units(run_id):
            rm.permanently_delete(f"{run_id}.u{u.doc_index}")

    def _raise_questions(
        self, cid: str, per_doc: list[tuple[str, DocDigest]], existing: list
    ) -> None:
        """#377: persist the clarification questions each document's digest raised
        (instead of hallucinating), after the deterministic guardrails —
        already-carded terms dropped, per-doc total capped. Term questions dedupe
        at collection level; description questions are doc-specific. Runs in the
        single finalize step so the non-CAS term dedup stays race-free."""
        carded = {nk for _, card in existing for nk in getattr(card, "norm_keys", [])}
        for doc_id, digest in per_doc:
            terms, descs = plan_doc_questions(
                digest.term_questions,
                digest.description_questions,
                carded_norm_keys=carded,
                cap=self._max_questions_per_doc,
            )
            for tq in terms:
                open_or_merge_term_question(
                    self._spec,
                    collection_id=cid,
                    term=tq.term,
                    source_doc_id=doc_id,
                    question_text=tq.question,
                )
            for dq in descs:
                add_description_question(
                    self._spec,
                    collection_id=cid,
                    source_doc_id=doc_id,
                    quote=dq.quote,
                    question_text=dq.question,
                )

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
