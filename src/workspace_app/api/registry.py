"""InvestigationRegistry — sticky per-investigation state for the API layer.

Why this exists: interrupt (Q10/c3), idle-kill (Q10/b1), and FS↔Sandbox
sync (Q11) all need a single source of truth per investigation — the
alive sandbox handle, the in-flight agent turn, and the last activity
timestamp. Today that state is per-request inside AgentToolContext,
which means every POST creates its own sandbox and there's no place to
cancel or expire from.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..quota.limits import ResourceLimits
from ..sandbox.protocol import (
    Sandbox,
    SandboxBusy,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)
from .sandbox_activity import IActivityStore
from .sandbox_address import IAddressStore

logger = logging.getLogger(__name__)


class _SyncHook(Protocol):
    """Subset of SandboxSync the registry calls. Lets tests inject a
    recorder without coupling the registry to the concrete SandboxSync.

    Param name stays `workspace_id` because FileStore + SandboxSync are
    domain-agnostic — they treat the identifier as an opaque namespace
    key, regardless of whether the caller calls it 'workspace' or
    'investigation'."""

    async def restore(
        self,
        workspace_id: str,
        handle: SandboxHandle,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int: ...
    async def mirror(self, workspace_id: str, handle: SandboxHandle) -> int: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class InvestigationSession:
    investigation_id: str
    handle: SandboxHandle | None = None
    # #674: the third-party bundles `handle` was CREATED with, `{name: sha}`.
    # `None` means UNKNOWN — this pod converged on a sandbox another pod built
    # (#366), so it never saw what went in. Known-empty (`{}`) and unknown are
    # deliberately different: only the first can say "that tool is not in here".
    tools: dict[str, str] | None = None
    last_active: datetime = field(default_factory=_utcnow)
    # Serializes sandbox creation (ensure_handle) for this investigation. Turn
    # lifecycle (the in-flight agent turn) lives in ChatTurnEngine, not here.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _bare_spec(_item: str) -> SandboxSpec:
    """The spec every item gets when nothing App-specific is wired — i.e. what
    this registry handed out before per-App resources existed."""
    return SandboxSpec()


@dataclass
class InvestigationRegistry:
    sandbox: Sandbox
    # The spec for ONE item, asked for at create time rather than fixed at
    # construction: an item's ceilings come from its App (`quota.limits`), and
    # the registry only learns which item it is serving when `_acquire` runs.
    # One source, not a constant-plus-override pair — two ways to answer the
    # same question is how they end up disagreeing.
    spec_for: Callable[[str], SandboxSpec] = _bare_spec
    # #674: this item's third-party bundles, `{name: sha}`, for the wakes that
    # have no turn behind them — the human terminal, a workflow's deterministic
    # node, the file-op rebuild. What a sandbox mounts is a property of the ITEM
    # (its App's declared `external_tools`), and it is fixed at create and never
    # rebuilt, so whichever entry point happens to wake it first must not get to
    # decide the item has no tools for that sandbox's whole life. A turn still
    # states its own — those are pinned to the resolve whose schemas the model
    # was handed. None ⇒ not wired (tests / no apps), and nothing is mounted.
    tools_for: Callable[[str], Awaitable[dict[str, str]]] | None = None
    # Who a live sandbox is charged to (the item's `owner` field). Wired for the
    # per-person limits; None ⇒ nothing is charged, and the heartbeat row stays
    # the plain liveness signal it was before.
    owner_of: Callable[[str], str | None] = lambda _item: None
    sync: _SyncHook | None = None
    # #345: global per-item activity heartbeat. When wired (shared-vol local
    # sandbox on multi-replica API), the idle reaper recycles a shared dir only
    # when GLOBALLY idle. None → single-process / non-shared behaviour (the dir
    # is reaped on pod-local idleness, as before).
    activity: IActivityStore | None = None
    # #366: per-item sandbox ADDRESS shared across pods. When wired (http backend,
    # whose handles are ephemeral + not id-addressable), pods converge on ONE
    # address per item instead of each minting a diverging sandbox. None → the
    # local shared-vol / single-process behaviour (the item-keyed dir converges).
    address: IAddressStore | None = None
    # #492: when the HOST owns durable (http + an NFS archive), the host rsyncs a
    # sandbox's working dir to/from the archive itself (host-local — can't hang
    # like the old per-file HTTP mirror). Then the app must NOT run its own
    # restore (the host restored on create) or mirror (write-back goes through
    # `sandbox.persist`). None/False → the app-side SandboxSync path (shared-vol
    # local / non-http), unchanged.
    host_managed_durable: bool = False
    # #492/M2: drain one item's durable store into the PHYSICAL tree the host
    # restores from, returning how many files it had to copy. Wired only for a
    # host-managed deployment whose durable store still spans two backends (the
    # `migrate_from` dual-read layer); None everywhere else, including once the
    # migration is retired. See `_acquire` for why it must run before `create`.
    durable_backfill: Callable[[str], Awaitable[int]] | None = None
    _sessions: dict[str, InvestigationSession] = field(default_factory=dict)

    @property
    def _has_durable(self) -> bool:
        """Whether a write-back has anywhere to go: the app-side SandboxSync
        mirror, OR (#492) the host's own rsync-to-NFS-archive. Gates every
        reconcile/checkpoint site so host-managed mode works even without an
        app-side sync wired (the write-back routes through `sandbox.persist`)."""
        return self.sync is not None or self.host_managed_durable

    async def _writeback(self, inv_id: str, handle: SandboxHandle, *, delete: bool) -> None:
        """#492: persist an item's live sandbox to durable. Host-managed ⇒ ask the
        host to rsync its own dir to the NFS archive (`delete` reconciles at a
        quiesced point; False is the additive mid-turn checkpoint). Else the
        app-side SandboxSync mirrors it, as before."""
        logger.debug(
            "registry: writeback item=%s handle=%s delete=%s host_managed=%s",
            inv_id,
            handle.id,
            delete,
            self.host_managed_durable,
        )
        if self.host_managed_durable:
            persist = getattr(self.sandbox, "persist", None)
            if persist is not None:
                await persist(handle, delete=delete)
            return
        # Every non-host-managed caller gates on `_has_durable`, which in this
        # branch (host_managed_durable False) means the app-side sync IS wired.
        assert self.sync is not None
        await self.sync.mirror(inv_id, handle)

    async def session(self, investigation_id: str) -> InvestigationSession:
        if investigation_id not in self._sessions:
            self._sessions[investigation_id] = InvestigationSession(
                investigation_id=investigation_id
            )
        return self._sessions[investigation_id]

    def _handle_for_id(self, investigation_id: str) -> SandboxHandle | None:
        """The shared-vol handle this backend would use for an item id, or None
        when it doesn't address by id (HTTP) — duck-typed so ad-hoc test doubles
        without the method simply route to the snapshot."""
        fn = getattr(self.sandbox, "handle_for_id", None)
        return fn(investigation_id) if fn is not None else None

    async def resolve_io_handle(self, investigation_id: str) -> SandboxHandle | None:
        """The handle a file op routes through, resolved GLOBALLY so reads AND
        writes hit the SAME source (#492) — WITHOUT waking a cold sandbox.

        Tiers: (1) this pod's live session handle; (2) http — the shared address
        store's published handle (``address.get``), so a NON-owning pod reads/
        writes the item's ONE live sandbox rather than a per-pod cold durable
        write the host's ``--delete`` mirror would later reconcile away; (3) local
        shared-vol — the id-derived handle (any pod resolves the same dir).

        ``None`` means no handle is published. For http that is ``¬P``, and since
        a live sandbox is ALWAYS published (``Q→P``, `_acquire` publishes after
        create), ``¬P`` proves the item is globally cold (``¬Q``) — so a durable
        write is safe (no sandbox exists to reconcile it). The returned handle is
        NOT liveness-probed here: the facade discovers liveness by running the op
        (a busy host retries, a gone one triggers `rebuild_io_handle`)."""
        s = self._sessions.get(investigation_id)
        if s is not None and s.handle is not None:
            return s.handle
        if self.address is not None:
            return await self.address.get(investigation_id)
        return self._handle_for_id(investigation_id)

    async def rebuild_io_handle(self, investigation_id: str) -> SandboxHandle:
        """Force a fresh live handle after a file op hit ``SandboxNotFound`` (the
        published sandbox was reaped/gone). #492: the facade calls this INSTEAD of
        cold-writing to durable — the item is globally warm (its address is
        published), so a cold write would be reconciled away by the host's
        ``--delete`` mirror. ``_acquire`` converges on another pod's live address
        or rebuilds from the durable archive and republishes (CAS)."""
        session = await self.session(investigation_id)
        async with session.lock:
            logger.info(
                "registry: rebuild io handle for item %s (file op hit sandbox-not-found)",
                investigation_id,
            )
            # #674: no turn behind a file op, so nothing to mount — and the
            # session records that as KNOWN-empty, not unknown. A turn that
            # later finds its tool missing from here then says so, instead of
            # handing the model a launcher this sandbox never received.
            session.handle, session.tools = await self._acquire(investigation_id)
            await self._bump(investigation_id)
            session.last_active = _utcnow()
        return session.handle

    async def ensure_handle(
        self,
        session: InvestigationSession,
        *,
        tools: dict[str, str] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SandboxHandle:
        # Lock so concurrent callers see a single Sandbox.create — without
        # this, N parallel POSTs to the same investigation would each spin
        # up their own container.
        #
        # #492 P11: `on_progress(done, total)` (the turn's restore-progress sink)
        # threads down to the app-side SandboxSync.restore on a cold wake, so a
        # slow restore streams "還原中 N/M" instead of a blank running card. None
        # on non-turn wakes (file-op rebuild) / host-managed mode (host rsyncs).
        async with session.lock:
            # #366 face A: a shared-address (http) session's cached handle may be
            # DEAD — the host reaped the sandbox out from under us (30-min idle
            # TTL / pod death). Probe it and re-acquire so the terminal never
            # execs a stale handle. Local shared-vol (address None) keeps the
            # create-once behaviour — its dir liveness is handled by #345 and
            # probing every wake would only churn.
            if session.handle is None or (
                self.address is not None and not await self._alive(session.handle)
            ):
                logger.debug(
                    "registry: ensure_handle acquiring sandbox for item %s (no live handle cached)",
                    session.investigation_id,
                )
                session.handle, session.tools = await self._acquire(
                    session.investigation_id, tools=tools, on_progress=on_progress
                )
            # Refresh the GLOBAL heartbeat on every wake/use (not just the first)
            # so another pod's idle reaper sees this item as live (#345).
            await self._bump(session.investigation_id)
            session.last_active = _utcnow()
        return session.handle

    async def would_cost(self, item: str) -> ResourceLimits:
        """What a NEW sandbox for `item` would really consume.

        The admission gate's "does one more of THIS size fit?" and the ledger's
        "what is already held?" are the same measurement asked at two moments,
        so they read the same source — the backend's own ceilings, not the
        App's declaration."""
        enforced = await self.sandbox.effective_limits(self.spec_for(item))
        return ResourceLimits(
            cpu_cores=enforced.cpu_cores,
            memory_bytes=enforced.memory_bytes,
            disk_bytes=0,  # admission weighs the flow dimensions only
        )

    async def running_items(self) -> list[str] | None:
        """Which items the BACKEND says have a sandbox running — or `None` when
        it cannot say.

        Everything else the app knows about live sandboxes is stored belief, and
        no record can be checked against another record. This is the one source
        that is not a record.

        Positive evidence only, and the `None` matters for the same reason: the
        hosted backend answers for the replica that took the request, so an item
        missing from the answer may simply be on another pod, and a failed call
        must never read as "nothing is running". Use it to FIND things, never to
        conclude that something is gone — for that, probe the item's own handle.
        """
        listed = await self.sandbox.running_sandboxes()
        if listed is None:
            return None
        # Deduped: an item can legitimately have two live sandboxes for a moment
        # (a #366 CAS loser before it kills its orphan; a rebuild after a probe
        # read a blip as death), and a caller counting what a person holds must
        # not charge them twice for one environment.
        return list(dict.fromkeys(e.item_id for e in listed if e.item_id))

    async def record_running(self, item: str) -> None:
        """Say that this item's sandbox is alive right now.

        The heartbeat is a lease, and re-arming one for something that is
        demonstrably running is not bookkeeping — it is what makes the person's
        limit count it and what stops another replica's reaper treating the
        directory as idle. Its natural caller is whoever just learned, from the
        backend itself, that a sandbox exists which no row named."""
        await self._bump(item)

    async def _bump(self, item: str) -> None:
        """Refresh the item's global heartbeat, carrying what its live sandbox
        costs and who owes it.

        Cost and debtor ride the SAME row as liveness on purpose: the per-person
        cpu/memory tally is only ever "sum the sandboxes that are alive", and a
        separate ledger would need its own answer to "is it alive" — two answers
        to that question is how a quota starts charging for things that are gone.

        The cost is what the BACKEND will enforce, not what the spec requested.
        A spec's `None` means "backend, apply your own ceiling", and every real
        backend does — a cgroup at `SANDBOX_HOST_CPU_CORES` / `sandbox.isolation.*`.
        Charging the request read those `None`s as zero, so an App that declared
        nothing held a core for free: `/my-resources` showed "CPU 0" beside a
        live environment, and a per-person cpu/memory cap summed zeros and could
        never bind."""
        if self.activity is None:
            return
        enforced = await self.sandbox.effective_limits(self.spec_for(item))
        await self.activity.bump(
            item,
            owner=self.owner_of(item) or "",
            cpu_milli=int((enforced.cpu_cores or 0) * 1000),
            memory_bytes=enforced.memory_bytes or 0,
        )

    async def _declared_tools(self, item: str) -> dict[str, str] | None:
        """What this item's App declares, for a wake with no turn to ask.

        Best effort: an artifact store that is down must not stop a person
        opening a terminal. The sandbox then comes up without that tool, which
        the session records as known-empty — so the next turn says WHY it is
        missing rather than handing the model a launcher that isn't there."""
        if self.tools_for is None:
            return None
        try:
            return await self.tools_for(item)
        except Exception:  # noqa: BLE001 - a wake must not fail on someone else's outage
            logger.warning(
                "registry: could not resolve third-party tools for item %s; "
                "creating the sandbox without them",
                item,
                exc_info=True,
            )
            return None

    async def _alive(self, handle: SandboxHandle) -> bool:
        """True when the sandbox behind ``handle`` still EXISTS — a cheap probe.

        Only a ``SandboxNotFound`` (reaped by the host / another pod, or the pod
        is gone) is 'rebuild'. A ``SandboxBusy`` (reachable but slow) means the
        sandbox is ALIVE — treat it as such (#492): rebuilding a merely-busy
        sandbox would spin up a SECOND live one (split-brain), so a transient
        overload must never be mistaken for death (the #493 g1 false-positive)."""
        try:
            await self.sandbox.exists(handle, "/")
        except SandboxNotFound:
            logger.debug("registry: probe handle %s -> not found (dead)", handle.id)
            return False
        except SandboxBusy:
            logger.debug("registry: probe handle %s -> busy (alive, not rebuilding)", handle.id)
            return True  # reachable but slow ⇒ it exists — do not rebuild
        return True

    async def _acquire(
        self,
        item: str,
        *,
        tools: dict[str, str] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> tuple[SandboxHandle, dict[str, str] | None]:
        """Materialise (or converge on) the item's single live sandbox handle.

        Returns the handle AND the third-party bundles it was created with —
        `None` when we converged on someone else's sandbox and so cannot know
        (#674). The caller keeps that on the session because a sandbox mounts
        its bundles once, at create, and a later turn has to be able to tell
        "not in there" from "no idea".

        #366: when an address store is wired (http backend), the handle is SHARED
        across pods — so first converge on an already-claimed address; else
        create + restore and CLAIM the shared slot (published AFTER restore), and
        a pod that loses the claim race kills its orphan and takes the winner.

        #345 restore-when-absent: probe BEFORE create so we restore from the
        snapshot ONLY when the dir doesn't exist — re-restoring over a live dir
        would resurrect files the agent deleted. A backend that mints its own
        handles (handle_for_id None, e.g. HTTP) is always a fresh create, so it
        always restores (the prior per-pod behaviour). Without an address store
        (local shared-vol / single-process) this is exactly that prior path."""
        stale: SandboxHandle | None = None
        if self.address is not None:
            existing = await self.address.get(item)
            if existing is not None:
                if await self._alive(existing):
                    logger.info(
                        "registry: acquire item %s -> converge on live address %s",
                        item,
                        existing.id,
                    )
                    # A live shared sandbox → converge on ONE. Another pod built
                    # it, so what it mounted is not ours to state.
                    return existing, None
                logger.info(
                    "registry: acquire item %s -> address %s dead, rebuilding",
                    item,
                    existing.id,
                )
                stale = existing  # dead address → rebuild + swap it out below
        fresh = await self._is_cold(item)
        # #492/M2: the host restores a sandbox by rsyncing the PHYSICAL durable
        # tree — it never reads through the app's FileStore, so the dual-read
        # migration layer's lazy per-file backfill never reaches it. Drain the
        # item into that tree FIRST or the sandbox comes up holding only the
        # files somebody happened to open, while the union `ls` still lists them
        # all, so nothing on screen looks wrong.
        #
        # This runs BEFORE `create` on purpose. These are the user's own files,
        # and a workspace that reaches the agent missing some of them is the
        # failure we refuse: work would accumulate on top of a partial state,
        # and once the migration retires the legacy store the gap is permanent.
        # Failing here leaves no sandbox at all, which is recoverable; a
        # half-filled one is not. (Seeding a NEW item's profile stays
        # best-effort — that is regenerable template, not the user's data.)
        if self.durable_backfill is not None:
            try:
                await self.durable_backfill(item)
            except Exception:
                logger.exception(
                    "registry: durable drain failed for item %s - refusing to build a "
                    "sandbox that would reach the agent missing the user's files",
                    item,
                )
                raise
        # #674: the item's own ceilings, plus the third-party bundles to mount.
        # A turn states them (pinned to the resolve whose schemas the model was
        # given); every other wake — terminal, workflow node, file-op rebuild —
        # says nothing and we ask what the ITEM declares, because mounting
        # happens once, at create, and cannot be repaired later. `{}` from a
        # caller is an ANSWER, not a gap: an app with no third-party tools must
        # not pay for a resolve on every wake.
        mounted = tools if tools is not None else await self._declared_tools(item)
        handle = await self.sandbox.create(
            replace(self.spec_for(item), tools=mounted), sandbox_id=item
        )
        logger.info(
            "registry: created sandbox handle %s for item %s (cold=%s)",
            handle.id,
            item,
            fresh,
        )
        # #492: in host-managed mode the host already restored this item's archive
        # into the fresh sandbox during create (and marked it ready), so the app
        # skips its own per-file restore. Otherwise restore from the durable
        # snapshot when the dir was cold, as before.
        if fresh and self.sync is not None and not self.host_managed_durable:
            logger.info(
                "registry: restoring item %s from durable snapshot into handle %s",
                item,
                handle.id,
            )
            await self.sync.restore(item, handle, on_progress=on_progress)
        if self.address is not None:
            # Publish the fresh address AFTER restore. Swap (CAS on the dead one)
            # when replacing a reaped address, else claim the empty slot; either
            # way the loser of a concurrent rebuild converges on the winner.
            winner = (
                await self.address.swap(item, expected=stale, new=handle)
                if stale is not None
                else await self.address.claim(item, handle)
            )
            if winner != handle:
                logger.info(
                    "registry: lost address CAS for item %s -> converge on %s (killing orphan %s)",
                    item,
                    winner.id,
                    handle.id,
                )
                await self.sandbox.kill(handle)  # lost the race — drop our orphan
                return winner, None  # the winner is someone else's build
            logger.info(
                "registry: won address CAS for item %s -> published handle %s",
                item,
                handle.id,
            )
        # We built it, so we can state what went in — `{}` included, which is
        # what lets a later turn say "that tool is not in here" rather than
        # offering the model a launcher that does not exist.
        return handle, dict(mounted or {})

    async def has_live_sandbox(self, investigation_id: str) -> bool:
        """Whether this item is ALREADY holding a live sandbox.

        Deliberately not `resolve_io_handle is not None`: that answers "where
        would I/O for this item go", and for an id-addressable backend it derives
        a handle whether or not anything is running there. Asking it about
        liveness would make every item look live, and a gate built on it would
        never refuse anything.

        This is `_is_cold` inverted — a real probe, plus the shared address for
        the http backend, which is the only place a live sandbox's existence is
        recorded across pods."""
        if investigation_id in self._sessions and self._sessions[investigation_id].handle:
            return True
        if self.address is not None:
            existing = await self.address.get(investigation_id)
            return existing is not None and await self._alive(existing)
        return not await self._is_cold(investigation_id)

    async def _is_cold(self, investigation_id: str) -> bool:
        """True when the item's sandbox dir is NOT yet materialized on shared
        storage (so a restore should seed it). Probes via the id-derived handle;
        a backend that isn't id-addressable is always treated as cold (a fresh
        create needs a restore)."""
        probe = self._handle_for_id(investigation_id)
        if probe is None:
            return True
        try:
            await self.sandbox.walk(probe, "/")
        except SandboxNotFound:
            return True
        return False

    async def flush(self, investigation_id: str) -> None:
        """Mirror this investigation's live sandbox to the snapshot right now
        (explicit refresh / turn-end). No-op when cold."""
        s = self._sessions.get(investigation_id)
        if s is None or s.handle is None or not self._has_durable:
            return
        logger.info("registry: flush write-back item %s (turn-end reconcile)", investigation_id)
        await self._writeback(investigation_id, s.handle, delete=True)  # turn-end reconcile

    async def mirror_warm(self) -> list[str]:
        """Throttle sweep: mirror every warm session to the snapshot via a
        version-diff (cheap when nothing changed — only changed files are
        downloaded). Run periodically (≤window) so a crash loses at most a
        window of work, and so files the shell created — which the file tools
        never see — still get persisted."""
        mirrored: list[str] = []
        for inv_id in list(self._sessions):
            s = self._sessions.get(inv_id)
            if s is None or s.handle is None or not self._has_durable:
                continue
            try:
                # #492: the periodic sweep is an ADDITIVE checkpoint (delete=False)
                # — mid-turn the dir isn't quiesced, so never reconcile deletions
                # here; turn-end / reap do that at a ready, settled sandbox.
                await self._writeback(inv_id, s.handle, delete=False)
            except Exception:  # noqa: BLE001 — #366: one bad item must not abort the sweep
                logger.warning(
                    "registry: mirror_warm skipped item %s (write-back failed)",
                    inv_id,
                    exc_info=True,
                )
                continue
            mirrored.append(inv_id)
        logger.debug("registry: mirror_warm swept, mirrored %d session(s)", len(mirrored))
        return mirrored

    async def sweep_uv_cache(self, max_bytes: int | None, threshold: timedelta) -> list[str]:
        """#775: bound the per-item uv download caches.

        Rides the idle tick because that is when a cache stops being written to,
        and delegates to the backend, which owns the directory.

        ⚠️ The in-use set is NOT just this pod's. `cache_keys_in_use` reads the
        backend's own `_dirs` — one process's view — while `{sandbox.root}` is a
        ReadWriteMany volume every replica writes to (#345). A pod that never
        created a sandbox for item X saw X as free and deleted the cache a sync
        on ANOTHER pod was filling, which is precisely the mistake `kill_idle`
        four lines below already guards against with the cross-pod heartbeat.
        So every candidate that is not locally live is checked against it too,
        and "no heartbeat wired" means single-process, where local IS global.

        Backends without a persistent cache (mock, docker, http — where the HOST
        sweeps its own) have no method and this is a no-op."""
        sweep = getattr(self.sandbox, "sweep_uv_cache", None)
        local = getattr(self.sandbox, "cache_keys_in_use", None)
        present = getattr(self.sandbox, "cache_keys_present", None)
        if sweep is None or local is None or present is None:
            return []
        in_use = set(local())
        if self.activity is not None:
            cutoff_ms = int((datetime.now(UTC) - threshold).timestamp() * 1000)
            for key in await asyncio.to_thread(present) - in_use:
                if not await self._globally_idle(key, cutoff_ms):
                    in_use.add(key)
        return await asyncio.to_thread(sweep, in_use=in_use, max_bytes=max_bytes)

    async def kill_idle(self, threshold: timedelta) -> list[str]:
        """Reap sandboxes idle past ``threshold``. #345: with a shared per-item
        dir, tearing it down (``rmtree`` via ``sandbox.kill``) on pod-local
        idleness alone would delete a dir another pod is still using. So when a
        global heartbeat is wired, a pod-locally-idle item whose dir is GLOBALLY
        active is only dropped from THIS pod's sessions — the dir is left for the
        active pod. The recycle (mirror→kill→forget) runs only when no pod has
        touched the item past the threshold."""
        cutoff = _utcnow() - threshold
        cutoff_ms = int(cutoff.timestamp() * 1000)
        killed: list[str] = []
        for inv_id in list(self._sessions):
            s = self._sessions[inv_id]
            if s.last_active >= cutoff:
                continue
            try:
                if s.handle is not None and not await self._globally_idle(inv_id, cutoff_ms):
                    logger.info(
                        "registry: kill_idle item %s locally idle but globally active "
                        "-> dropped local session, kept shared dir",
                        inv_id,
                    )
                    # Another pod is live on the shared dir — drop our local session
                    # only, leave the dir (and its heartbeat) intact.
                    del self._sessions[inv_id]
                    continue
                if s.handle is not None:
                    if self._has_durable:
                        # write-back before rmtree (reconcile — the dir is settled)
                        await self._writeback(inv_id, s.handle, delete=True)
                    # #366: a handle the host already reaped (idle TTL) raises
                    # SandboxNotFound — that IS the goal, so still drop the session.
                    with contextlib.suppress(SandboxNotFound):
                        await self.sandbox.kill(s.handle)
                    if self.activity is not None:
                        await self.activity.forget(inv_id)
                    logger.info(
                        "registry: reaped idle sandbox %s for item %s (globally idle)",
                        s.handle.id,
                        inv_id,
                    )
                del self._sessions[inv_id]
                killed.append(inv_id)
            except Exception:  # noqa: BLE001 — #366: one bad item must not abort the sweep
                logger.warning(
                    "registry: kill_idle skipped item %s (reap failed)",
                    inv_id,
                    exc_info=True,
                )
                continue
        return killed

    async def _globally_idle(self, investigation_id: str, cutoff_ms: int) -> bool:
        """True when no pod has touched the item's shared dir since ``cutoff_ms``.
        No heartbeat wired ⇒ True (single-process: pod-local idleness is global)."""
        if self.activity is None:
            return True
        ms = await self.activity.last_active_ms(investigation_id)
        return ms is None or ms < cutoff_ms

    async def close_all(self) -> None:
        """Shutdown: tear down every sandbox this pod is holding.

        Per item, like `kill_idle` and `mirror_warm`: one sandbox the host had
        already reaped (its own idle TTL, a restart) raises `SandboxNotFound`
        here, and letting that abort the loop leaked every session after it —
        on the one path whose whole job is to leave nothing behind. Already gone
        is the goal, so it is not even a warning.

        Anything else IS a warning, and stops that one item rather than killing
        past it: a kill can rmtree the item's shared dir, so proceeding after a
        write-back we know did not land trades a slow shutdown for lost work."""
        logger.info("registry: close_all reaping %d session(s)", len(self._sessions))
        for inv_id in list(self._sessions):
            s = self._sessions.pop(inv_id)
            if s.handle is None:
                continue
            try:
                if self._has_durable:
                    # A write-back that fails for any reason OTHER than "the
                    # sandbox is already gone" stops this item here, KEEPING the
                    # sandbox: killing it can rmtree the item's shared dir, and
                    # doing that on top of a durable snapshot we know is stale
                    # is how shutdown turns a bad minute into lost work. The dir
                    # outlives this process; the next pod warms it.
                    try:
                        await self._writeback(inv_id, s.handle, delete=True)
                    except SandboxNotFound:
                        continue  # already gone — nothing to mirror, nothing to kill
                with contextlib.suppress(SandboxNotFound):
                    await self.sandbox.kill(s.handle)
            except Exception:  # noqa: BLE001 — one bad item must not strand the rest
                logger.warning(
                    "registry: close_all left item %s behind (teardown failed)",
                    inv_id,
                    exc_info=True,
                )

    async def close_session(self, investigation_id: str) -> None:
        """Tear down one item's sandbox, from ANY replica.

        Used by the panel's Close button and by the close-investigation endpoint.

        **Finding it.** Three sources, in order, because each covers what the one
        before it cannot:

        1. this pod's session — but `_sessions` is one pod's memory, so a close
           that landed on a different replica (or after a restart) found nothing;
        2. the shared address — including when the session's own handle is stale,
           which happens on its own whenever the host reaps on its idle timer or
           a pod dies (`ensure_handle` already probes for exactly this);
        3. what the backend says it is actually RUNNING — the last resort, and
           the only thing that can reach a sandbox whose address was lost.

        **Clearing the heartbeat.** It goes whenever this returns — including
        when no source could name a sandbox at all. The heartbeat is a record of
        what the app believes is running, and if nothing the app can reach is
        running for this item, continuing to charge for it is charging for a
        belief nothing supports. `SandboxNotFound` counts as gone for the same
        reason `kill_idle` and `_alive` already read it that way; #492 already
        carved out the case where the sandbox IS alive, and it has its own
        signal — a reachable-but-slow host raises `SandboxBusy`, which
        propagates from here before anything is cleared (503, retry).

        Refusing to clear when nothing was found is the version of this that got
        written first, and it was worse than the bug it was meant to prevent.
        The listing covers only the host replica that answered, so a genuinely
        orphaned sandbox is found with probability 1/N — and on a miss the
        person got a 204 saying it worked, a row that stayed, and a slot they
        could not free for the whole `idle_timeout` window (8 hours by default).
        Over-clearing has a way back: the panel asks the backend what is running
        and re-arms what it finds (`record_running`). Under-clearing has none.

        **The address is never cleared here**, exactly as `kill_idle` never
        clears it. It does not need to be: `_acquire` probes a published address
        and CAS-swaps a dead one for the sandbox it builds, so a stale row costs
        nothing. Deleting it is what carries risk — the delete cannot be made
        conditional on the handle we killed (specstar's `delete` takes no
        expected-etag), so a peer that rebuilt the item while this teardown was
        in flight would have ITS live address erased, and the next acquire would
        build a second sandbox beside it. That is the split-brain the address
        store exists to prevent.

        The session's cached handle is taken UNDER its lock and before the
        teardown. Popping the session first and then writing back + killing loses
        writes, because #345 keys the sandbox dir to the ITEM: a request arriving
        in that window built a new session with its own new lock, warmed the SAME
        dir, wrote into it, and then this method's `kill` rmtree'd the dir
        underneath an already-acknowledged write. Holding the lock means a
        concurrent file op either lands before the write-back (so it is mirrored)
        or waits and then finds `handle is None` and re-acquires a fresh dir.

        The lock covers only what this pod cached. A handle found through the
        address or the listing belongs to whichever replica warmed it, and
        nothing here serialises against that pod — which is inherent to closing
        something another process owns.
        """
        s = self._sessions.get(investigation_id)
        handle: SandboxHandle | None = None
        killed_here = False
        # Each source can name a sandbox an earlier one already tried, and a
        # teardown is not free — it rsyncs the whole workspace to the durable
        # archive before killing. Carrying the ids keeps the fallbacks a search
        # for something NEW rather than a retry of the same handle.
        tried: set[str] = set()
        if s is not None:
            async with s.lock:
                # Re-read under the lock: a concurrent close may have finished
                # and replaced the session. Its sandbox still has to be torn
                # down, so the search below carries on either way.
                if self._sessions.get(investigation_id) is s:
                    handle, s.handle = s.handle, None
                if handle is not None:
                    # INSIDE the lock — see the paragraph above. Taking the
                    # handle under it and then tearing down outside restores the
                    # #345 race in full: the window between the two is exactly
                    # where a concurrent file op re-warms the same item dir and
                    # writes into it, and this kill then rmtrees the dir under
                    # an acknowledged write.
                    tried.add(handle.id)
                    killed_here = await self._teardown(investigation_id, handle)

        # A session with no handle at all is the NORMAL state, not an edge case:
        # sandboxes are lazy, so every pod that has served one chat turn holds
        # one. And a cached handle can simply be out of date. Both land here —
        # which is why the fallbacks are gated on "did WE end one", not on
        # "is it gone": a stale handle reports gone and the live sandbox the
        # address names is still running.
        if not killed_here and self.address is not None:
            published = await self.address.get(investigation_id)
            if published is not None and published.id not in tried:
                tried.add(published.id)
                killed_here = await self._teardown(investigation_id, published)

        if not killed_here:
            killed_here = await self._close_unrecorded(investigation_id, tried)

        if self.activity is not None:
            await self.activity.forget(investigation_id)
        # Pop only a session that is still the one we emptied. `ensure_handle`
        # may have re-acquired into the SAME object while the teardown ran (the
        # lock is released before it), and popping that drops a live handle on
        # the floor; a concurrent close may have replaced the object outright.
        current = self._sessions.get(investigation_id)
        if current is not None and current is s and current.handle is None:
            del self._sessions[investigation_id]
        elif s is None:
            self._sessions.pop(investigation_id, None)

    async def _close_unrecorded(self, item: str, tried: set[str]) -> bool:
        """Tear down a sandbox for `item` that no record of ours names.

        This is how an orphan gets cleared. A sandbox whose address was lost —
        an app pod that died between `create` and the CAS publish, a record
        wiped by an older build of this method — is invisible to everything the
        app stores, while it keeps running and keeps costing its owner from a
        row that either is not there or has nothing behind it. Asking the
        backend what it is really running is the only way to reach it.

        Matched on item id, never on "the only one running": the answer names
        every sandbox on the pod that took the request, and picking the wrong
        entry closes a stranger's environment mid-turn.

        `tried` names handles an earlier source already tore down, so this stays
        a search for something NEW: the listing usually still names the sandbox
        the session just failed to kill, and re-running the teardown would rsync
        the whole workspace to the archive a second time for nothing.

        True if any match was killed here. A backend that cannot say (`None`)
        reports the same as an empty answer — this only ever ADDS a way to find
        something, so its silence leaves the earlier sources to it.
        """
        listed = await self.sandbox.running_sandboxes()
        if not listed:
            return False
        killed = False
        for entry in listed:
            if entry.item_id == item and entry.handle.id not in tried:
                tried.add(entry.handle.id)
                killed = await self._teardown(item, entry.handle) or killed
        return killed

    async def _teardown(self, item: str, handle: SandboxHandle) -> bool:
        """Persist and kill one sandbox. True when THIS call completed the kill.

        The answer steers the SEARCH and nothing else: while no source has
        actually ended a sandbox, the next source is still worth asking. That is
        why it is not "is it gone" — a stale cached handle is gone, and the
        sandbox the published address names is still running, so stopping there
        would leave the live one behind.

        It governs no deletion, so being wrong about it costs a redundant lookup
        rather than a record. `SandboxNotFound` is therefore just False: the
        sandbox is not there (the same reading `kill_idle` and `_alive` already
        give that signal), and the caller carries on looking. The case where the
        sandbox IS alive has its own signal — a reachable-but-slow host raises
        `SandboxBusy` (#492), which propagates from here untouched so every
        record stays put and the caller is told to retry (503).

        A write-back that cannot reach the sandbox means the kill has nothing to
        act on, so it stops there. A write-back that SUCCEEDS says nothing about
        the kill: they are separate calls to the same host and only the second
        frees the machine."""
        if self._has_durable:
            try:
                await self._writeback(item, handle, delete=True)
            except SandboxNotFound:
                logger.info(
                    "registry: sandbox %s for item %s was already gone at write-back",
                    handle.id,
                    item,
                )
                return False
        logger.info("registry: close_session killing sandbox %s for item %s", handle.id, item)
        try:
            await self.sandbox.kill(handle)
        except SandboxNotFound:
            logger.info("registry: sandbox %s for item %s was already gone", handle.id, item)
            return False
        return True
