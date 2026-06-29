"""WikiMaintenanceCoordinator (#50 P3 / #59) — the ingest→wiki hook.

When a document finishes indexing, a collection with ``use_wiki`` enabled
should fold that source into its LLM wiki. This coordinator is the seam the
upload/sync/reindex routes call after ``Ingestor.index`` completes.

**Durable, cross-pod queue (#59).** The work runs on a specstar job queue, not
an in-process asyncio queue: ``on_doc_indexed`` enqueues one
``WikiMaintenanceJob`` per source (``partition_key`` = the collection id) and a
background consumer (``start_consume(block=False)``) drains it. That buys three
things the old in-memory queue couldn't:

  - **multipod** (#58): jobs are specstar resources on the shared backend, so
    every pod consumes the same queue.
  - **per-collection serialisation across pods**: specstar only hands out one
    job per ``partition_key`` at a time, so a collection's maintenance is
    strictly serial even with many consumers — no two pods race its pages.
  - **durability**: a job survives a restart (it's a resource, not RAM).

Karpathy-faithful: one source folded per run, so the maintainer integrates
incrementally rather than batch-rebuilding.

**Build status** is a durable ``WikiBuildState`` resource (one per collection)
rather than an in-memory dict, so ``GET /wiki/status`` is coherent whichever
pod serves it. ``building`` and the done-count are DERIVED from the live count
of PENDING/PROCESSING jobs (correct across retries + pods); only ``total`` (per
build epoch) and the live ``current`` / ``phase`` / ``errors`` are stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import msgspec
from specstar import QB, Schema, SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    RevisionStatus,
    TaskStatus,
)

from ...resources import AgentConfig, Collection, SourceDoc, WikiBuildState
from ..job_audit import preserve_job_creator
from .code_wiki import CodeWikiBuilder
from .guidance import with_collection_guidance
from .jobs import WikiJobPayload, WikiMaintenanceJob
from .maintainer import (
    default_wiki_maintainer_config,
    default_wiki_unfolder_config,
    run_wiki_maintainer,
)
from .sources import SpecstarWikiSources
from .store import WikiFileStore

if TYPE_CHECKING:
    from ...api.events import AgentEvent
    from ...api.runner import AgentRunner
    from ..llm import ILlm

_LOGGER = logging.getLogger(__name__)

# Coarse activity for the live build UI, derived from the maintainer's CURRENT
# tool call — honest (it reflects what the agent is actually doing), not a
# fabricated checklist. read_* = ingesting the source; search/list_files/read_file
# = locating affected pages; write/edit = writing pages.
_PHASE_BY_TOOL = {
    "read_new_source": "reading",
    "read_source": "reading",
    "list_sources": "reading",
    "search_wiki": "identifying",
    "list_files": "identifying",
    "read_file": "identifying",
    "write_file": "writing",
    "edit_file": "writing",
    "delete_file": "writing",
}

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02  # aclose() poll cadence while waiting for the queue to drain


def _acting_as(wiki_store: WikiFileStore, user: str | None):
    """``wiki_store.acting_as(user)`` (#83), or a no-op when there's no user to
    stamp (e.g. a near-impossible race where the source vanished before we could
    read its updater) — so the run still proceeds, just under the default user."""
    return wiki_store.acting_as(user) if user is not None else contextlib.nullcontext()


# The user-turn instruction for an un-fold pass (#43): a source was deleted, so
# take it back OUT of the wiki (the unfolder system prompt has the detail).
_UNFOLD_INSTRUCTION = (
    "A source was REMOVED from the collection. Scrub it from the wiki: revise or "
    "delete pages that cited or relied on it, and drop it from any `Sources:` lines."
)


@dataclass
class WikiBuildStatus:
    """Live progress of a collection's wiki maintenance, for the FE's
    "Updating…" UI. ``total``/``done`` count sources in the current build
    batch; ``current`` is the source being folded; ``phase`` is the coarse
    current activity (reading / identifying / writing). ``errors`` /
    ``last_error`` surface terminal run failures so a maintainer that writes
    nothing is never silent."""

    building: bool = False
    total: int = 0
    done: int = 0
    current: str | None = None
    phase: str | None = None
    errors: int = 0
    last_error: str | None = None


class WikiMaintenanceCoordinator:
    """Per-collection wiki-maintenance scheduler, backed by a specstar job
    queue (cross-pod serial via ``partition_key``)."""

    def __init__(
        self,
        spec: SpecStar,
        runner: AgentRunner,
        *,
        agent_config: AgentConfig | None = None,
        unfolder_config: AgentConfig | None = None,
        maintainer_max_turns: int = 40,
        message_queue_factory: object | None = None,
        get_user_id: Callable[[], str] | None = None,
        code_wiki_llm: ILlm | None = None,
    ) -> None:
        self._spec = spec
        self._runner = runner
        self._wiki_store = WikiFileStore(spec)
        # #281: a code collection (one with a git_url) builds its wiki by reading
        # source hierarchically via CodeWikiBuilder instead of folding one source
        # at a time. None ⇒ no code-wiki LLM wired ⇒ a code build records an error.
        self._code_builder = (
            CodeWikiBuilder(spec, code_wiki_llm, wiki_store=self._wiki_store)
            if code_wiki_llm is not None
            else None
        )
        # #186: who an enqueue is credited to — the real user in a request
        # (resolved off the spec default any non-job manager still carries);
        # production injects the same get_user_id.
        self._get_user_id = get_user_id or (lambda: spec.get_resource_manager(SourceDoc).user)
        self._agent_config = agent_config or default_wiki_maintainer_config()
        self._unfolder_config = unfolder_config or default_wiki_unfolder_config()
        self._maintainer_max_turns = maintainer_max_turns
        self._doc_rm = spec.get_resource_manager(SourceDoc)
        self._coll_rm = spec.get_resource_manager(Collection)
        self._state_rm = spec.get_resource_manager(WikiBuildState)
        # The job model's handler needs runtime deps (runner / configs), so it
        # can't be registered in make_spec — register it here. The queue
        # backend MUST be set PER-MODEL (a global `configure(message_queue_
        # factory=)` doesn't propagate to a real pg/disk backend), so pass the
        # config-selected factory straight to add_model. Default = the
        # specstar-backed Simple queue (multipod via the shared backend).
        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(WikiMaintenanceJob, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(WikiMaintenanceJob)
        # #186: preserve each maintenance job's creator across its lifecycle
        # instead of stamping the worker default; producers below set the user
        # explicitly via using().
        preserve_job_creator(self._job_rm)
        self._consuming = False

    # ── status (read) ────────────────────────────────────────────────
    def status(self, collection_id: str) -> WikiBuildStatus:
        """The collection's current build progress (idle default when none).
        ``building`` + ``done`` are derived from the live job count so they're
        correct across retries and multiple consumers."""
        try:
            state = self._state_rm.get(collection_id).data
        except ResourceIDNotFoundError:
            return WikiBuildStatus()
        assert isinstance(state, WikiBuildState)
        active = self._active_count(collection_id)
        building = active > 0
        return WikiBuildStatus(
            building=building,
            total=state.total,
            done=max(0, state.total - active),
            current=state.current if building else None,
            phase=state.phase if building else None,
            errors=state.errors,
            last_error=state.last_error,
        )

    def _active_count(self, collection_id: str | None = None) -> int:
        q = QB["status"].in_(_ACTIVE)
        if collection_id is not None:
            q = q & (QB["partition_key"] == collection_id)
        return self._job_rm.count_resources(q.build())

    # ── enqueue (producer) ───────────────────────────────────────────
    async def on_doc_indexed(self, doc_id: str, *, requested_by: str | None = None) -> None:
        """Enqueue ``doc_id``'s source for its collection's wiki if (and only
        if) that collection has ``use_wiki`` on. Returns immediately — the
        maintenance runs in the background consumer (this or another pod).

        ``requested_by`` (#186) is the user this enqueue is credited to. A route
        calls this directly inside a request and leaves it ``None`` → the current
        request user; the index worker passes the index run's requester
        explicitly (it has no request)."""
        try:
            doc = self._doc_rm.get(doc_id).data
        except ResourceIDNotFoundError:
            return  # doc vanished (deleted before the hook fired)
        assert isinstance(doc, SourceDoc)  # the SourceDoc manager yields a SourceDoc (ty narrow)
        cid = doc.collection_id
        try:
            coll = self._coll_rm.get(cid).data
        except ResourceIDNotFoundError:
            return
        if not (isinstance(coll, Collection) and coll.use_wiki):
            return  # wiki path not enabled for this collection

        actor = requested_by if requested_by is not None else self._get_user_id()
        # #281: a code collection rebuilds its whole wiki hierarchically, so a
        # per-source fold is wrong — enqueue (or coalesce onto) a single build.
        if coll.git_url:
            self._enqueue_code_build(cid, actor)
            return
        # Start (or grow) the build epoch's source counter. A fresh batch (no
        # active jobs) resets the counter so the FE shows "1/N" not "29/N".
        # #186: the build-state row + the job are both credited to the requester
        # (the job manager carries no default, so its create MUST set a user).
        with self._state_rm.using(user=actor), self._job_rm.using(user=actor):
            fresh = self._active_count(cid) == 0
            self._update_state(
                cid,
                lambda s: (
                    WikiBuildState(collection_id=cid, total=1)
                    if fresh
                    else msgspec.structs.replace(s, total=s.total + 1)
                ),
            )
            self._job_rm.create(
                WikiMaintenanceJob(
                    payload=WikiJobPayload(collection_id=cid, source_path=doc.path, doc_id=doc_id),
                    partition_key=cid,
                )
            )

    async def trigger_code_build(
        self, collection_id: str, *, requested_by: str | None = None
    ) -> None:
        """Enqueue (or coalesce onto) a single code-wiki build for a code
        collection. This is the EXPLICIT trigger the sync endpoint / code-sync
        sweeper / use_wiki toggle / manual rebuild call after a code collection's
        sources change — ``code_repo.sync`` ingests synchronously and bypasses
        the IndexCoordinator, so ``on_doc_indexed`` never fires on those paths
        (#281 A0). No-op unless the collection is a code-wiki collection (has a
        ``git_url`` AND ``use_wiki`` on); mirrors ``on_doc_indexed``'s code branch
        without a per-doc lookup."""
        try:
            coll = self._coll_rm.get(collection_id).data
        except ResourceIDNotFoundError:
            return
        if not (isinstance(coll, Collection) and coll.use_wiki and coll.git_url):
            return  # not a code-wiki collection — nothing to build
        actor = requested_by if requested_by is not None else self._get_user_id()
        self._enqueue_code_build(collection_id, actor)

    def _enqueue_code_build(self, cid: str, actor: str) -> None:
        """Queue one ``code_build`` for the collection, coalescing onto any build
        already in flight (a sync ingests many files; we want ONE rebuild, not one
        per file). With no code-wiki LLM wired, record the misconfiguration on the
        build state instead of silently doing nothing."""
        if self._code_builder is None:
            with self._state_rm.using(user=actor):
                self._update_state(
                    cid,
                    lambda s: msgspec.structs.replace(
                        s, last_error="code-wiki LLM not configured (set kb.wiki.llm)"
                    ),
                )
            return
        with self._job_rm.using(user=actor):
            if self._has_active_code_build(cid):
                return  # a build is already queued/running for this collection
            self._job_rm.create(
                WikiMaintenanceJob(
                    payload=WikiJobPayload(collection_id=cid, source_path="", op="code_build"),
                    partition_key=cid,
                )
            )

    def _has_active_code_build(self, cid: str) -> bool:
        q = (QB["status"].in_(_ACTIVE) & (QB["partition_key"] == cid)).build()
        return any(
            isinstance(r.data, WikiMaintenanceJob) and r.data.payload.op == "code_build"
            for r in self._job_rm.list_resources(q)
        )

    async def on_doc_deleted(self, doc_id: str) -> None:
        """A source is being deleted — enqueue an un-fold pass to scrub its
        traces from the collection's wiki, but only if the wiki path is on.
        Snapshots the source's display label + text NOW (the caller invokes this
        BEFORE the row is hard-deleted), because the remove-pass cannot re-read a
        gone doc. Returns immediately; the scrub runs in the background consumer,
        serialised after any in-flight fold for the same collection
        (``partition_key``)."""
        try:
            doc = self._doc_rm.get(doc_id).data
        except ResourceIDNotFoundError:
            return  # already gone
        assert isinstance(doc, SourceDoc)
        cid = doc.collection_id
        try:
            coll = self._coll_rm.get(cid).data
        except ResourceIDNotFoundError:
            return
        if not (isinstance(coll, Collection) and coll.use_wiki):
            return  # wiki path not enabled — nothing to un-fold
        if coll.git_url:
            # #281 A1: a code collection's wiki is built hierarchically by
            # CodeWikiBuilder, not by per-source prose folds — so the prose
            # unfolder must NOT run on a delete (it would garble the code wiki).
            # Deletion deliberately does not auto-rebuild either (a rebuild per
            # deleted file is wasteful); the orphaned /files page is pruned by the
            # next rebuild's reconcile step.
            return
        ref = SpecstarWikiSources(self._spec, cid).ref_by_id(doc_id)
        if ref is None:  # pragma: no cover — race: doc deleted between the get above and here
            return
        # #186: this runs inside the deleter's request — credit the unfold job to
        # them (the job manager has no default, so the create MUST set a user).
        # The downstream scrub reads job.created_by as `triggered_by`.
        with self._job_rm.using(user=self._get_user_id()):
            self._job_rm.create(
                WikiMaintenanceJob(
                    payload=WikiJobPayload(
                        collection_id=cid,
                        source_path=doc.path,
                        doc_id=doc_id,
                        op="unfold",
                        removed_label=ref.path,
                        removed_text=ref.text,
                    ),
                    partition_key=cid,
                )
            )

    def _ensure_consuming(self) -> None:
        """Start this process's background consumer once (idempotent).

        Deliberately NOT called from ``on_doc_indexed``: a burst of enqueues
        must all be on the queue before any of them is drained, else a fast
        consumer could finish source 1 before source 2 is enqueued and source
        2 would look like a fresh build epoch (resetting the N/M counter).
        create_app calls ``start_consuming`` once at startup; ``aclose`` starts
        it to flush. So enqueue and consume are decoupled."""
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        self._ensure_consuming()

    @property
    def consuming(self) -> bool:
        """Whether the background consumer is running (#312) — observable so the
        API's ``run_consumers`` gate can be asserted and a worker can report it
        is draining its JobType."""
        return self._consuming

    def _stop_consuming(self) -> None:
        """Tear down the background consumer thread (it's a daemon, but we stop
        it explicitly so a process that builds many coordinators — the test
        suite — doesn't accumulate idle consumer threads spinning on empty
        queues). Restartable via ``_ensure_consuming``. Only called by
        ``aclose`` with a consumer known to be running."""
        self._consuming = False
        # message_queue is always set (the job model is registered with a
        # factory); stop_consuming joins the daemon so it's gone on return.
        self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]

    # ── consume (handler — runs in the queue's consumer thread) ──────
    def _handle(self, job) -> None:  # job: Resource[WikiMaintenanceJob]
        """Run one wiki pass OFF the main loop (the consumer's own thread),
        driving the async agent with a fresh event loop. Dispatches fold vs
        un-fold (#43); exceptions are recorded + swallowed by the pass — one bad
        source must not wedge the partition. Returning normally → COMPLETED."""
        payload = job.data.payload
        # #186: this maintenance job's creator is the requester (the deleter for
        # an unfold, the indexer/uploader for a fold) — preserved across the job
        # lifecycle now the manager carries no default. Credit every WikiBuildState
        # write in this pass to them. (WikiPage writes keep their own acting-user
        # inside the handlers: the source's updater for a fold, the deleter for an
        # unfold — #83.)
        actor = job.info.created_by
        with self._state_rm.using(user=actor):
            if payload.op == "code_build":
                self._handle_code_build(payload, triggered_by=actor)
            elif payload.op == "unfold":
                # #83: the source is gone by now, so there's no updater to
                # preserve — credit the scrub to whoever triggered it (the job's
                # creator, i.e. the user who deleted the source).
                self._handle_unfold(payload, triggered_by=actor)
            else:
                self._handle_fold(payload)

    def _maintainer_config(self, cid: str, base: AgentConfig) -> AgentConfig:
        """Append the collection's maintainer guidance (#90) onto ``base`` — the
        bundled maintainer/unfolder prompt — so the operator's domain/structure
        guidance rides on top of the machinery. Both fold and unfold use the
        same maintainer guidance. A vanished collection falls back to ``base``."""
        try:
            coll = self._coll_rm.get(cid).data
        except ResourceIDNotFoundError:  # pragma: no cover — collection deleted mid-job
            return base
        assert isinstance(coll, Collection)  # the Collection manager yields a Collection (ty)
        return with_collection_guidance(base, coll.wiki_maintainer_guidance)

    def _handle_fold(self, payload) -> None:
        """Fold one source into its collection's wiki, resolving the EXACT doc
        by id — not a path scan, which would fold the first match for two
        same-path docs (silent drop). ``ref.path`` is the display label
        (disambiguated by uploader on collision)."""
        cid, path = payload.collection_id, payload.source_path
        sources = SpecstarWikiSources(self._spec, cid)
        self._update_state(cid, lambda s: msgspec.structs.replace(s, current=path, phase="reading"))
        ref = sources.ref_by_id(payload.doc_id)
        if ref is not None:
            new_source = f"Source path: {ref.path}\n\n{ref.text}"
            # #83: this runs in a job pod with no request user. Credit the wiki
            # page writes to the SOURCE's last updater (the uploader), not the
            # bare worker default.
            try:
                folder = self._doc_rm.get(payload.doc_id).info.updated_by
            except (
                ResourceIDNotFoundError
            ):  # pragma: no cover — doc vanished post-ref, a near-impossible race
                folder = None
            try:
                with _acting_as(self._wiki_store, folder):
                    asyncio.run(
                        run_wiki_maintainer(
                            self._runner,
                            wiki_store=self._wiki_store,
                            wiki_sources=sources,
                            collection_id=cid,
                            new_source=new_source,
                            agent_config=self._maintainer_config(cid, self._agent_config),
                            max_turns=self._maintainer_max_turns,
                            on_event=self._phase_tracker(cid),
                        )
                    )
            except Exception:
                _LOGGER.exception("wiki maintainer run failed for %s:%s", cid, path)
                self._update_state(
                    cid,
                    lambda s: msgspec.structs.replace(
                        s,
                        errors=s.errors + 1,
                        last_error=s.last_error or "the maintainer run failed",
                    ),
                )
        self._update_state(cid, lambda s: msgspec.structs.replace(s, current=None, phase=None))

    def _handle_unfold(self, payload, *, triggered_by: str) -> None:
        """Scrub a DELETED source from the wiki. The doc row is gone, so the
        pass runs off the snapshot (label + text) taken at enqueue time — same
        machinery as a fold, but with the unfolder config + a remove
        instruction. ``triggered_by`` is the deleter (the job's creator); the
        page writes are credited to them (#83), since there's no source updater
        left to preserve."""
        cid, label = payload.collection_id, payload.removed_label
        self._update_state(
            cid, lambda s: msgspec.structs.replace(s, current=label, phase="reading")
        )
        removed = f"Source path: {label}\n\n{payload.removed_text}"
        try:
            with self._wiki_store.acting_as(triggered_by):
                asyncio.run(
                    run_wiki_maintainer(
                        self._runner,
                        wiki_store=self._wiki_store,
                        wiki_sources=SpecstarWikiSources(self._spec, cid),
                        collection_id=cid,
                        new_source=removed,
                        agent_config=self._maintainer_config(cid, self._unfolder_config),
                        max_turns=self._maintainer_max_turns,
                        on_event=self._phase_tracker(cid),
                        instruction=_UNFOLD_INSTRUCTION,
                    )
                )
        except Exception:
            _LOGGER.exception("wiki unfold run failed for %s:%s", cid, label)
            self._update_state(
                cid,
                lambda s: msgspec.structs.replace(
                    s,
                    errors=s.errors + 1,
                    last_error=s.last_error or "the unfold run failed",
                ),
            )
        self._update_state(cid, lambda s: msgspec.structs.replace(s, current=None, phase=None))

    def _handle_code_build(self, payload, *, triggered_by: str) -> None:
        """Rebuild a code collection's whole wiki by reading its source
        hierarchically (#281). Page writes are credited to the build's triggerer;
        errors are recorded + swallowed so one bad build never wedges the
        partition."""
        cid = payload.collection_id
        if self._code_builder is None:  # pragma: no cover — a code_build is enqueued
            # only by a pod that HAS a builder; this guards a 2nd pod missing kb.wiki.llm
            self._update_state(
                cid,
                lambda s: msgspec.structs.replace(
                    s, errors=s.errors + 1, last_error="code-wiki LLM not configured"
                ),
            )
            return
        self._update_state(
            cid, lambda s: msgspec.structs.replace(s, current="the code wiki", phase="building")
        )
        try:
            with self._wiki_store.acting_as(triggered_by):
                asyncio.run(self._code_builder.build(cid))
        except Exception:
            _LOGGER.exception("code wiki build failed for %s", cid)
            self._update_state(
                cid,
                lambda s: msgspec.structs.replace(
                    s,
                    errors=s.errors + 1,
                    last_error=s.last_error or "the code wiki build failed",
                ),
            )
        self._update_state(cid, lambda s: msgspec.structs.replace(s, current=None, phase=None))

    def _phase_tracker(self, cid: str) -> Callable[[AgentEvent], None]:
        """Map the maintainer's live tool calls to a coarse build phase, and
        capture terminal failures (step-limit / error). Writes only on phase
        CHANGE so a 40-turn run costs ~3 status writes, not 40."""
        last: dict[str, str | None] = {"phase": None}

        def on_event(ev: AgentEvent) -> None:
            kind = getattr(ev, "type", "")
            if kind == "tool_start":
                phase = _PHASE_BY_TOOL.get(getattr(ev, "name", ""))
                if phase is not None and phase != last["phase"]:
                    last["phase"] = phase
                    self._update_state(cid, lambda s: msgspec.structs.replace(s, phase=phase))
            elif kind == "max_turns_exceeded":
                turns = getattr(ev, "turns", "?")
                self._update_state(
                    cid,
                    lambda s: msgspec.structs.replace(
                        s,
                        errors=s.errors + 1,
                        last_error=f"hit the step limit ({turns} turns) before finishing — "
                        "raise kb.wiki.maintainer_max_turns",
                    ),
                )
            elif kind == "error":
                msg = getattr(ev, "message", "the maintainer run failed")
                self._update_state(
                    cid,
                    lambda s: msgspec.structs.replace(s, errors=s.errors + 1, last_error=msg),
                )

        return on_event

    # ── WikiBuildState CAS upsert (cross-thread safe) ────────────────
    def _update_state(self, cid: str, mutate: Callable[[WikiBuildState], WikiBuildState]) -> None:
        """Read→mutate→write the collection's build-state row under optimistic
        concurrency, so the producer (total) and consumer (phase/errors) never
        clobber each other's fields. On a CAS conflict, re-read + retry — the
        loser of a race rebases instead of clobbering. Draft writes keep status
        churn out of revision history. (A conflict is only reachable under real
        cross-thread / cross-pod contention.)"""
        while True:
            try:
                res = self._state_rm.get(cid)
                etag: str | None = res.info.etag
                assert isinstance(res.data, WikiBuildState)
                current = res.data
            except ResourceIDNotFoundError:
                etag, current = None, WikiBuildState(collection_id=cid)
            new = mutate(current)
            try:
                if etag is None:
                    self._state_rm.create(
                        new,
                        status=RevisionStatus.draft,
                        resource_id=cid,
                        if_not_exists=True,  # ty: ignore[unknown-argument]
                    )
                else:
                    self._state_rm.modify(
                        cid,
                        new,
                        status=RevisionStatus.draft,
                        expected_etag=etag,  # ty: ignore[unknown-argument]
                    )
                return
            except (
                DuplicateResourceError,
                PreconditionFailedError,
                ResourceIDNotFoundError,
            ):  # pragma: no cover — concurrent-writer race, not deterministically reproducible
                continue  # re-read + retry on the winner's new etag

    # ── lifecycle ────────────────────────────────────────────────────
    async def aclose(self) -> None:
        """Await all in-flight maintenance (graceful shutdown / test sync) by
        polling until the queue drains. The consumer is a daemon thread (it
        dies with the process); we just wait for it to finish outstanding
        jobs. Starts the consumer first so a coordinator that never called
        ``start_consuming`` (direct-construction tests) still flushes. Stops
        the consumer once drained so we don't leak idle daemon threads."""
        # Nothing queued and no consumer running ⇒ no-op (don't spin up a
        # thread just to tear it down — that also races stop against start).
        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
