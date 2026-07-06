"""Background-task lifecycle for the FastAPI app (#54 app.py split).

Lifts the server's startup/shutdown orchestration out of ``create_app``: the
five background sweepers (idle reaper, code-sync, FileStore mirror, #227 index
fan-out recovery, #245 blob GC) plus the ``lifespan`` asynccontextmanager that
runs the fast health probes, starts the in-process queue consumers (when
``run_consumers`` is on), launches the gated background tasks, and drains them
(plus the coordinators and kernels) on shutdown.

``HealthService`` and ``_persist_check_run`` stay in ``app.py``; this module
receives the already-built ``health_service`` and the per-deploy schedule knobs
as injected params. The coordinators (wiki / index / sanity / card_gen) are read
off ``app.state.*`` inside ``lifespan`` at startup/shutdown — not captured here —
because they are wired onto the app after construction.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import FastAPI
from specstar import SpecStar

from ..filestore.blob_gc import register_gc_lease, run_blob_gc
from ..health.service import HealthService
from ..kernels import KernelService
from ..observability.boot import boot_step
from .registry import InvestigationRegistry
from .sandbox_activity import register_sandbox_activity
from .sandbox_address import register_sandbox_address

if TYPE_CHECKING:
    from ..filestore.protocol import FileStore
    from ..monitor import IMonitor

# #227: how often the background index-sweeper recovers stuck fan-out runs, and
# how long a run may go without progress before its missing batches are declared
# failed. The grace must exceed one batch's worst case (≈ the broker's 30-min
# consumer-ack timeout) so a slow-but-live run is never falsely failed.
INDEX_SWEEP_INTERVAL_S = 300.0
INDEX_STUCK_AFTER_S = 3600.0
# #479: how often the daily-reflection sweeper wakes to check whether any prose
# wiki collection is due (the actual cadence is once-a-day per collection, gated on
# last_reflected_at; this is just the poll granularity, like the code-sync sweeper).
_WIKI_REFLECT_CHECK_INTERVAL_S = 300.0


def _utcnow() -> datetime:
    """Timezone-aware wall clock in UTC — the trigger sweeper's ``now_utc`` (each schedule
    converts it into its own zone for the period math)."""
    return datetime.now(UTC)


def build_lifespan(
    *,
    registry: InvestigationRegistry,
    spec: SpecStar,
    kernels: KernelService,
    health_service: HealthService,
    filestore: FileStore,
    monitor: IMonitor,
    run_consumers: bool,
    idle_timeout: timedelta,
    idle_check_interval: timedelta,
    mirror_interval: timedelta,
    max_workspace_bytes: int,
    code_sync_check_interval: timedelta | None,
    code_daily_sync: str | None = None,
    wiki_reflect_daily: str | None = None,
    gc_interval: timedelta | None,
    gc_t1: str,
    gc_t2: str,
    trigger_check_interval: timedelta | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build the FastAPI ``lifespan`` context manager, capturing the injected
    deps in the nested sweeper closures. The coordinators stay off-capture and
    are read from ``app.state.*`` inside ``lifespan``."""

    async def idle_killer() -> None:
        """Periodically reap sandboxes whose last_active is past the
        threshold. The reaper sleeps the check_interval between sweeps
        — short for tests, ~60 s in production.

        #345: the same tick also enforces the scratch-vol soft quota — any item
        whose working dir grew past ``max_workspace_bytes`` is recycled (even if
        not idle), so one runaway workspace can't fill the shared scratch volume.
        Gated on a positive cap (0 ⇒ no measurement, no overhead)."""
        try:
            while True:
                await asyncio.sleep(idle_check_interval.total_seconds())
                await registry.kill_idle(idle_timeout)
                if max_workspace_bytes > 0:
                    await registry.enforce_quota(max_workspace_bytes)
        except asyncio.CancelledError:
            return

    async def code_sync_sweeper(app: FastAPI) -> None:
        """Enqueue a ``code_sync`` job for every code Collection due for its
        daily wall-clock sync (#355). The sweeper is a pure producer — the clone +
        ingest + wiki build all run in the enqueued job on the wiki worker (#312),
        not here on the API. ``app`` is passed in (vs captured) so the wiki
        coordinator — built after the FastAPI app — is read from
        ``app.state.wiki_coordinator`` post-construction, symmetric with
        ``index_sweeper``'s coordinator lookup."""
        from ..kb.code_repo import CodeRepoSweeper

        assert code_sync_check_interval is not None  # gated by caller
        sweeper = CodeRepoSweeper(
            spec,
            enqueue=app.state.wiki_coordinator.enqueue_code_sync,
            daily_sync=code_daily_sync,
        )
        try:
            while True:
                await asyncio.sleep(code_sync_check_interval.total_seconds())
                # tick enqueues code_sync jobs; the jobs themselves clone+ingest
                # and chain into the build (no per-cid trigger needed here).
                await asyncio.to_thread(sweeper.tick)
        except asyncio.CancelledError:
            return

    async def reflect_sweeper(app: FastAPI) -> None:
        """#479: enqueue a ``reflect`` job for every prose wiki Collection due for
        its daily wall-clock consolidation. A pure producer — the survey/plan/apply
        runs in the enqueued job on the wiki worker (#312), not here. Read
        ``app.state.wiki_coordinator`` post-construction, like ``code_sync_sweeper``.
        Gated on ``wiki_reflect_daily`` being set (caller)."""
        from ..kb.wiki.reflect_sweeper import WikiReflectSweeper

        sweeper = WikiReflectSweeper(
            spec,
            enqueue=app.state.wiki_coordinator.enqueue_reflect,
            reflect_daily=wiki_reflect_daily,
        )
        try:
            while True:
                # Tick first (then sleep): a pod that starts after the daily time
                # catches up its due collections immediately instead of waiting a
                # whole poll interval; the once-a-day gate prevents a re-run.
                await asyncio.to_thread(sweeper.tick)
                await asyncio.sleep(_WIKI_REFLECT_CHECK_INTERVAL_S)
        except asyncio.CancelledError:
            return

    async def mirror_sweeper() -> None:
        """Throttle: every ~mirror_interval, persist any warm sandbox the agent
        wrote to since the last sweep into the FileStore snapshot. Coalesces a
        burst of agent writes into one mirror; a crash loses at most a window."""
        try:
            while True:
                await asyncio.sleep(mirror_interval.total_seconds())
                await registry.mirror_warm()
        except asyncio.CancelledError:
            return

    async def index_sweeper(app: FastAPI) -> None:
        """#227: periodically recover stuck index fan-outs — a lost finalize
        trigger (winner crashed) or a dead-lettered batch — so a doc never wedges
        in 'indexing'. Queue-agnostic (CAS), idempotent, and cheap (one indexed
        query), run off the loop since it does blocking specstar I/O. ``app`` is
        passed in (vs captured) since the FastAPI app is created after this
        builder; ``app.state.index_coordinator`` is wired post-construction."""
        try:
            while True:
                await asyncio.sleep(INDEX_SWEEP_INTERVAL_S)
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        app.state.index_coordinator.sweep_stuck_runs,
                        stuck_after_seconds=INDEX_STUCK_AFTER_S,
                    )
        except asyncio.CancelledError:
            return

    async def blob_gc_sweeper() -> None:
        """#245: periodically reclaim orphaned blobs (deleted files' content) via
        specstar's ref-count GC, so the per-workspace quota stays honest. A CAS
        lease means only ONE pod runs the full (deleting) reconcile per window;
        the others no-op. Run off the loop — reconcile does blocking specstar I/O.
        ``gc_interval`` gates this caller (None ⇒ no task)."""
        assert gc_interval is not None
        ttl_ms = int(gc_interval.total_seconds() * 1000)
        try:
            while True:
                await asyncio.sleep(gc_interval.total_seconds())
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        run_blob_gc, spec, t1=gc_t1, t2=gc_t2, ttl_ms=ttl_ms, monitor=monitor
                    )
                    # #407: on the same durable-maintenance cadence, snapshot the
                    # WorkspaceFile cardinality (total rows / distinct workspaces /
                    # largest workspace) so the ws_census trend shows whether the
                    # per-file model grows unbounded — the archive-vs-keep signal.
                    census = await filestore.census()  # ty: ignore[unresolved-attribute]
                    monitor.record({"kind": "ws_census", "t": int(time.time() * 1000), **census})
        except asyncio.CancelledError:
            return

    async def trigger_sweeper(app: FastAPI) -> None:
        """#429 P7: the schedule-trigger poll loop. Each tick re-scans every app/profile's
        ``triggers.json`` (so an operator edit takes effect without a restart), and for each
        enabled schedule that is due, a CAS claim elects ONE pod to launch the run under the
        trigger's declared ``acting_user``. The whole tick swallows its own errors so one bad
        profile / a transient specstar blip never wedges the loop. ``app`` is passed in (vs
        captured) so the orchestrator — built after the FastAPI app — is read from
        ``app.state.workflow_orchestrator``, symmetric with ``index_sweeper``."""
        from ..workflow.triggers import (
            OrchestratorOrphanOps,
            SpecstarTriggerStore,
            TriggerSweeper,
            build_trigger_start,
            discover_schedule_triggers,
        )

        assert trigger_check_interval is not None  # gated by caller
        orchestrator = app.state.workflow_orchestrator
        sweeper = TriggerSweeper(
            load=discover_schedule_triggers,
            store=SpecstarTriggerStore(spec),
            start=build_trigger_start(orchestrator.start),
            now_utc=_utcnow,
            # #429 P8: chase orphaned triggered runs (a pod died mid-run) — resume from the
            # journal, then abandon to a discoverable terminal once the resume budget is spent.
            orphan=OrchestratorOrphanOps(orchestrator),
        )
        try:
            while True:
                await asyncio.sleep(trigger_check_interval.total_seconds())
                with contextlib.suppress(Exception):
                    await sweeper.tick()
        except asyncio.CancelledError:
            return

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Issue #51 / Q2: the fast (connectivity-grade) probes block
        # boot — an operator sees a dead embedder before the first
        # request. The heavy capability round (LLM/VLM/agent probes) is
        # NOT auto-run at boot; it stays on-demand (FE re-run /
        # POST /health/checks/run) so startup only verifies basic
        # connectivity instead of hammering the local model every boot.
        # #208: each step narrates (→/✓) so a stall in the lifespan names itself
        # instead of looking like a silent hang.
        with boot_step("health: connectivity checks"):
            await asyncio.to_thread(health_service.run_fast_sync)
        # #312: in-process consumers run only when `run_consumers` is on. Default
        # True keeps the all-in-one behaviour; a pod-split deploy sets it False so
        # the API is a pure producer and dedicated worker pods drain each JobType.
        # The shared, partitioned queues drain regardless of which process
        # enqueued, so a worker pod (or another all-in-one pod) picks the jobs up.
        if run_consumers:
            # #59: wiki-maintenance consumer. Idempotent + non-blocking.
            with boot_step("start wiki-maintenance consumer"):
                app.state.wiki_coordinator.start_consuming()
            # #82: indexing consumer (so a slow embed never starves the request path).
            with boot_step("start indexing consumer"):
                app.state.index_coordinator.start_consuming()
            # Model-sanity battery consumer (when wired) — drains SanityRun jobs.
            if app.state.sanity_coordinator is not None:
                with boot_step("start model-sanity consumer"):
                    app.state.sanity_coordinator.start_consuming()
            # #175: context-card generation consumer.
            with boot_step("start context-card generation consumer"):
                app.state.card_gen_coordinator.start_consuming()
        # #230: seed the platform Help collection from packaged content (repo =
        # source of truth; identical bytes are a no-op). Ingestion needs the
        # embedder, so it runs here (off the loop) and is best-effort — a dead
        # embedder leaves the collection readable-but-unindexed, never blocking
        # boot. The id is stashed for the /help route. #281 will later feed
        # source-code-derived wiki into this same collection. The ingestor is
        # read off app.state (built after the FastAPI app, like the coordinators).
        from ..kb.help_collection import HELP_SYSTEM_USER, seed_help_collection_best_effort

        with boot_step("seed help collection"):
            app.state.help_collection_id = await asyncio.to_thread(
                seed_help_collection_best_effort,
                spec,
                app.state.ingestor,
                user=HELP_SYSTEM_USER,
            )
        # #345: register the shared per-item activity-heartbeat model (only when
        # the registry uses it — the local shared-vol sandbox). Registered HERE,
        # after spec.apply, so its CRUD routes are never emitted (same reason as
        # the #245 blob-GC lease below).
        if registry.activity is not None:
            register_sandbox_activity(spec)
        # #366: register the shared per-item sandbox-address model (only when the
        # registry uses it — the HTTP sandbox-host backend). Same post-apply
        # timing so its CRUD routes are never emitted.
        if registry.address is not None:
            register_sandbox_address(spec)
        # #429 P9: the event-trigger processing high-water model (idempotent + the D2d
        # discoverable-lag ledger). Registered post-apply so its CRUD routes are never emitted,
        # like the coordination models above. Event dispatch is in-request (not swept), so this
        # is wired whenever the app runs — it just no-ops when no event triggers are declared.
        from ..workflow.event_dispatch import register_event_watermark

        register_event_watermark(spec)
        bg = [asyncio.create_task(idle_killer()), asyncio.create_task(mirror_sweeper())]
        bg.append(asyncio.create_task(index_sweeper(app)))  # #227 fan-out stuck-run recovery
        # NOTE: the full capability round is deliberately NOT scheduled here
        # — boot stays connectivity-only (see the health step above); operators
        # trigger the heavy round on demand via the FE / POST /health/checks/run.
        if code_sync_check_interval is not None:
            bg.append(asyncio.create_task(code_sync_sweeper(app)))
        if wiki_reflect_daily:
            # #479: daily wiki-reflection producer (a code collection is skipped by
            # the sweeper; "" / null in kb.wiki.reflect_daily disables it here).
            bg.append(asyncio.create_task(reflect_sweeper(app)))
        if gc_interval is not None:
            # #245: seed the CAS lease, then run the orphan-blob GC on a schedule.
            register_gc_lease(spec)
            bg.append(asyncio.create_task(blob_gc_sweeper()))
        if trigger_check_interval is not None:
            # #429 P7: register the shared window-ledger model (post-apply, so its CRUD
            # routes are never emitted — same reason as the blob-GC lease above), then run
            # the schedule-trigger poll loop.
            from ..workflow.triggers import register_trigger_store

            register_trigger_store(spec)
            bg.append(asyncio.create_task(trigger_sweeper(app)))
        try:
            yield
        finally:
            for t in bg:
                t.cancel()
            for t in bg:
                with contextlib.suppress(BaseException):
                    await t
            # Drain in-flight wiki maintenance before exit (bounded). Pending
            # jobs are durable — they survive to be picked up after restart.
            with contextlib.suppress(BaseException):
                await app.state.wiki_coordinator.aclose()
            with contextlib.suppress(BaseException):
                await app.state.index_coordinator.aclose()
            if app.state.sanity_coordinator is not None:
                with contextlib.suppress(BaseException):
                    await app.state.sanity_coordinator.aclose()
            with contextlib.suppress(BaseException):
                await app.state.card_gen_coordinator.aclose()
            await kernels.shutdown_all()
            await registry.close_all()

    return lifespan
