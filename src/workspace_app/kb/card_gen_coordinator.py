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

import msgspec
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
    card_proposal_id,
    classify_against_existing,
    ensure_proposal_ids,
    is_active,
    merge_drafts,
)
from .card_gen_run import CardGenRunStore
from .card_gen_sources import CardGenSources
from .card_proposal import CardProposalStore
from .context_cards import cards_with_ids_for_collections, derive_norm_keys
from .doc_questions import (
    add_description_question,
    open_or_merge_term_question,
    plan_doc_questions,
)
from .job_audit import preserve_job_creator
from .reconcile import Reconciler

_LOGGER = logging.getLogger(__name__)

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02  # aclose() poll cadence while waiting for the queue to drain

# The run's own lifecycle string → the TaskStatus the FE polls (#414: status lives
# on the run, not on any one fanned-out job). #511 P2: run.status is now PURELY the
# generation lifecycle — the review-resolution terminals (committed/dismissed) are
# gone; a run leaves the 待審核 queue when its proposals go terminal, not the run.
_RUN_STATUS = {
    "pending": TaskStatus.PENDING,
    "running": TaskStatus.PROCESSING,
    "done": TaskStatus.COMPLETED,
    "error": TaskStatus.FAILED,
}


def _existing_card(rm, resource_id: str) -> ContextCard | None:
    """The card a commit-time UPDATE targets, or ``None`` when it was deleted since
    generation (the caller falls back to a create). Returns the card rather than a bool
    so the overwrite can carry the target's server-owned / human-curated fields forward
    without a second read."""
    try:
        data = rm.get(resource_id).data
    except ResourceIDNotFoundError:
        return None
    assert isinstance(data, ContextCard)  # narrow Struct|Unset for ty
    return data


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
        reconciler: Reconciler | None = None,
    ) -> None:
        self._spec = spec
        self._drafter = drafter
        # #506 P6: the finalize-time semantic reconcile (suppress already-explained
        # candidates, cluster cross-run duplicates). None → the pre-P6 exact-only
        # behaviour (tests / a build with no embedder).
        self._reconciler = reconciler
        self._runs = CardGenRunStore(spec)
        # #511 P1: each kept proposal is ALSO projected to a first-class
        # CardProposal row so the review inbox pages at the DB (the nested
        # CardGenRun.proposals list stays as a read-only fallback for now).
        self._proposals = CardProposalStore(spec)
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

    def set_drafter(self, drafter: CardDrafter) -> None:
        """#506: swap the drafter in after construction. The agentic drafter is
        built from the KB retriever + a subagent bridge, which exist only AFTER the
        coordinators are built (create_app ordering), so create_app constructs this
        coordinator with the fallback drafter then swaps in the agentic one here.
        Safe: called synchronously during create_app, before any consumer starts."""
        self._drafter = drafter

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
        """The run's reviewable proposals (empty until it finalizes), each carrying
        its stable ``pid`` so the review table can address one card. Read from the
        first-class :class:`CardProposal` rows (#511 P2), not the nested list."""
        return CardGenArtifact(proposals=self._proposals.list_by_run(run_id))

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    # ── review state (persisted on the run, #175 Q7) ─────────────────
    def save_review(
        self, run_id: str, proposals: list[ProposedCard], *, reviewer: str | None = None
    ) -> None:
        """Persist the reviewer's edited / decided proposals, so leaving the review
        page and returning restores progress. Upserts each proposal's first-class
        :class:`CardProposal` row (#511 P2) with what the FE sends back; a run whose
        collection cascaded away is a no-op."""
        run = self._runs.get(run_id)
        if run is None:
            return
        self._proposals.replace_run_proposals(
            run.collection_id, run_id, ensure_proposal_ids(list(proposals))
        )

    def _write_card(self, cardrm, cid: str, p: ProposedCard, result: CommitResult) -> None:
        """Write one accepted proposal to a real ``ContextCard`` (#106 author/edit):
        ``new`` → create, ``update`` → overwrite the target (re-deriving
        ``norm_keys``); a target deleted since generation falls back to a create."""
        card = ContextCard(
            collection_id=cid,
            keys=p.keys,
            norm_keys=derive_norm_keys(p.keys),
            title=p.title,
            body=p.body,
        )
        target = _existing_card(cardrm, p.target_card_id) if p.target_card_id else None
        if p.mode == "update" and target is not None:
            # New COMMITTED revision of the target (create_or_update, not modify —
            # modify refuses a committed→committed overwrite).
            # #518: a proposal has no notion of linked documents, so this full-struct
            # overwrite must carry the target's curated links across — else every
            # card-gen round strips the evidence off the cards it refreshes.
            cardrm.create_or_update(
                p.target_card_id,
                msgspec.structs.replace(card, reference_doc_ids=list(target.reference_doc_ids)),
            )
            result.updated += 1
        else:
            # New card — or the update target vanished since generation.
            cardrm.create(card)
            result.created += 1

    def commit(self, run_id: str, *, committed_by: str | None = None) -> CommitResult:
        """Write the run's ACCEPTED proposals to real cards and resolve the run out
        of the queue once nothing active remains (#415/#481). Proposals the reviewer
        didn't accept — and any with no usable key — are skipped. Returns the
        tallies. Per-run convenience over :meth:`commit_cards`."""
        run = self._runs.get(run_id)
        if run is None or run.status != "done":
            return CommitResult()  # gone or never finalized — no card-write
        accepted = [p.id for p in self._proposals.list_by_run(run_id) if p.decision == "accepted"]
        return self._commit_run_cards(run_id, run, accepted, committed_by, skip_unreferenced=True)

    def commit_cards(
        self, cards: list[tuple[str, str]], *, committed_by: str | None = None
    ) -> CommitResult:
        """#481: write an arbitrary set of proposal cards, given as ``(run_id,
        card_id)`` refs, and resolve each affected run if it settles. Per-run commit
        is just the special case where every ref shares a run. Refs are grouped by
        run so each run's cards are written + marked committed together. Returns the
        aggregated tallies."""
        by_run: dict[str, list[str]] = {}
        for run_id, card_id in cards:
            by_run.setdefault(run_id, []).append(card_id)
        total = CommitResult()
        for run_id, card_ids in by_run.items():
            run = self._runs.get(run_id)
            if run is None or run.status != "done":
                continue  # gone or already reviewed
            r = self._commit_run_cards(run_id, run, card_ids, committed_by, skip_unreferenced=False)
            total.created += r.created
            total.updated += r.updated
            total.skipped += r.skipped
        return total

    def _commit_run_cards(
        self,
        run_id: str,
        run,
        card_ids: list[str],
        committed_by: str | None,
        *,
        skip_unreferenced: bool,
    ) -> CommitResult:
        """Write the referenced ACTIVE proposals of one run to cards, mark them
        committed, and settle the run. ``skip_unreferenced`` counts every other
        proposal as skipped (the whole-run ``commit`` reports one tally over the run);
        the multi-card path counts only the refs it was given."""
        wanted = set(card_ids)
        cid = run.collection_id
        cardrm = self._spec.get_resource_manager(ContextCard)
        result = CommitResult()
        written: list[str] = []
        with cardrm.using(user=committed_by or self._get_user_id()):
            for p in self._proposals.list_by_run(run_id):
                if p.id not in wanted:
                    if skip_unreferenced:
                        result.skipped += 1
                    continue
                if not is_active(p) or not derive_norm_keys(p.keys):
                    result.skipped += 1  # rejected / already committed / no usable key
                    continue
                self._write_card(cardrm, cid, p, result)
                written.append(p.id)
        # #511 P2: advance the written proposals' CardProposal rows to committed (per
        # proposal, CAS). Once a run has no active proposal left it drops out of the
        # queue on its own — no run.status settle.
        self._proposals.mark_committed([card_proposal_id(run_id, pid) for pid in written])
        return result

    def decide(self, run_id: str, card_id: str, decision: str) -> None:
        """#481: persist one proposal's decision (inline accept/reject) by id. The run
        leaves the queue automatically once its last active proposal is resolved
        (#511 P2: the queue is "runs with an active CardProposal", no run.status flip)."""
        self._proposals.set_decision(card_proposal_id(run_id, card_id), decision)

    def update_proposal(self, run_id: str, card_id: str, card: ProposedCard) -> None:
        """#481: persist the reviewer's edited proposal (drawer edit: body/title +
        decision) by id, onto its first-class :class:`CardProposal` row (#511 P2)."""
        self._proposals.update(card_proposal_id(run_id, card_id), card)

    def dismiss(self, run_id: str) -> None:
        """#415: discard a run's proposals without writing any card — reject every
        ACTIVE proposal so the run leaves the 待審核 queue (#511 P2: no run.status
        terminal; the queue is "runs with an active CardProposal")."""
        self._proposals.dismiss_run(run_id)

    def pending_runs(self, collection_id: str) -> list[CardGenRunSummary]:
        """The collection's runs that still hold an ACTIVE proposal — the 待審核 queue
        rows (#511 P2: "runs with an active CardProposal", not a run.status), newest
        first. ``proposal_count`` is the run's active count. The FE lazy-loads each
        run's proposals on expand."""
        return [
            CardGenRunSummary(run_id=rid, collection_id=collection_id, proposal_count=count)
            for rid, count in self._proposals.active_runs(collection_id)
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
            _LOGGER.info(
                "CardGen: doc %s not resolvable (deleted before run %s, index %d) — "
                "nothing to digest",
                doc_id,
                run_id,
                doc_index,
            )
            self._runs.mark_done(run_id, doc_index)  # doc deleted before run — nothing to digest
            return
        if not ref.ready:
            # Still indexing (a binary doc carries no extracted text until it
            # flips to "ready"), so digesting now would silently draft 0 cards.
            # Skip it. If the collection has auto_digest on, the index-completion
            # hook (#377) drafts it once ready; otherwise the user re-generates
            # after indexing (the modal says so). Either way, never draft empty.
            _LOGGER.info(
                "CardGen: doc %s (run %s, index %d) still indexing — skipped; the "
                "auto-digest hook will draft it once it's ready",
                doc_id,
                run_id,
                doc_index,
            )
            self._runs.mark_done(run_id, doc_index)  # close the slot; defer to the hook
            return
        try:
            # #506: the agentic drafter scopes its ask_knowledge_base to the doc's
            # OWN collection, so pass it down (the one-shot drafter ignores it).
            digest = self._drafter.digest(
                doc_path=ref.path, doc_text=ref.text, collection_id=ref.collection_id
            )
        except Exception:  # noqa: BLE001 — one doc's give-up must not sink the run
            _LOGGER.exception("CardGen: digest failed for doc %s (run %s)", doc_id, run_id)
            self._runs.mark_failed(run_id, doc_index)
            return
        # #494 observability: a doc that HAS text but digests to nothing is the
        # silent zero-output failure — surface it (WARNING) with the doc + text
        # length instead of a falsely-green run; a healthy digest logs at INFO.
        n_cards = len(digest.cards)
        n_questions = len(digest.term_questions) + len(digest.description_questions)
        if ref.text.strip() and not (n_cards or n_questions):
            _LOGGER.warning(
                "CardGen: doc %s (run %s, index %d) has text (%d chars) but digested "
                "to 0 cards and 0 questions — the drafter/LLM produced nothing usable",
                doc_id,
                run_id,
                doc_index,
                len(ref.text),
            )
        else:
            _LOGGER.info(
                "CardGen: doc %s (run %s, index %d) → %d cards, %d questions (text=%d chars)",
                doc_id,
                run_id,
                doc_index,
                n_cards,
                n_questions,
                len(ref.text),
            )
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
        # #506 P6: semantic reconcile over the exact-classified survivors — suppress
        # already-explained candidates (near an existing card / documented in the
        # wiki) and cluster cross-run duplicates. No reconciler → exact-only (pre-P6).
        if self._reconciler is not None:
            kept = self._reconciler.reconcile_proposals(cid, run_id, kept, existing)
        # #511 P2: stamp ids up front so the first-class CardProposal rows share the
        # SAME prop:{run}:{pid} id as the reconcile ClusterMember (reconcile ran the
        # same ensure_proposal_ids, so this is a no-op there — but a no-reconciler
        # build needs it before projecting). The CardProposal rows are now the SOLE
        # store — the nested CardGenRun.proposals write is gone (dropped in P5).
        kept = ensure_proposal_ids(kept)
        for p in kept:
            self._proposals.create_from_proposal(cid, run_id, p)
        self._raise_questions(cid, per_doc, existing)
        self._clear_staged(run_id)
        # All documents failed (none digested) → the run failed; else it produced
        # proposals (possibly partial, if some docs failed) → done.
        all_failed = run.total > 0 and len(run.done) == 0
        status = "error" if all_failed else "done"
        # #494 observability: one structured line records the whole funnel so a run
        # that produced nothing (0 proposals over N text-bearing docs) is
        # diagnosable end-to-end without re-deriving it from scattered state.
        n_questions = sum(len(d.term_questions) + len(d.description_questions) for _, d in per_doc)
        _LOGGER.info(
            "CardGen finalize: run=%s n_units=%d n_raw_drafts=%d n_proposals=%d "
            "n_questions=%d total=%d done=%d failed=%d final_status=%s",
            run_id,
            len(units),
            len(raw),
            len(kept),
            n_questions,
            run.total,
            len(run.done),
            len(run.failed),
            status,
        )
        self._runs.finish(run_id, status=status)

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
        term_items: list[tuple[str, Callable[[], str]]] = []
        for doc_id, digest in per_doc:
            terms, descs = plan_doc_questions(
                digest.term_questions,
                digest.description_questions,
                carded_norm_keys=carded,
                cap=self._max_questions_per_doc,
            )
            for tq in terms:
                # Defer the actual open() to the reconciler — it opens ONLY the terms it
                # doesn't suppress (already explained in wiki / covered by a card, ③⑥).
                term_items.append(
                    (
                        tq.term,
                        lambda tq=tq, doc_id=doc_id: open_or_merge_term_question(
                            self._spec,
                            collection_id=cid,
                            term=tq.term,
                            source_doc_id=doc_id,
                            question_text=tq.question,
                        ),
                    )
                )
            for dq in descs:
                add_description_question(
                    self._spec,
                    collection_id=cid,
                    source_doc_id=doc_id,
                    quote=dq.quote,
                    question_text=dq.question,
                )
        # #506 ⑤/③⑥: reconcile ALL raised terms in one batch — grade each against the
        # wiki + existing cards (wiki loaded once), suppress the already-explained ones
        # (recorded as auditable members, never opened), open + cluster the rest so a
        # question groups with a proposal for the same concept. Pre-P6 (no reconciler):
        # just open everything, no suppression.
        if self._reconciler is not None:
            self._reconciler.reconcile_term_questions(cid, term_items)
        else:
            for _term, open_q in term_items:
                open_q()

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
