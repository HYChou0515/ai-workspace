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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..sandbox.protocol import Sandbox, SandboxBusy, SandboxHandle, SandboxNotFound, SandboxSpec
from .sandbox_activity import IActivityStore
from .sandbox_address import IAddressStore


class _SyncHook(Protocol):
    """Subset of SandboxSync the registry calls. Lets tests inject a
    recorder without coupling the registry to the concrete SandboxSync.

    Param name stays `workspace_id` because FileStore + SandboxSync are
    domain-agnostic — they treat the identifier as an opaque namespace
    key, regardless of whether the caller calls it 'workspace' or
    'investigation'."""

    async def restore(self, workspace_id: str, handle: SandboxHandle) -> int: ...
    async def mirror(self, workspace_id: str, handle: SandboxHandle) -> int: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class InvestigationSession:
    investigation_id: str
    handle: SandboxHandle | None = None
    last_active: datetime = field(default_factory=_utcnow)
    # Serializes sandbox creation (ensure_handle) for this investigation. Turn
    # lifecycle (the in-flight agent turn) lives in ChatTurnEngine, not here.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class InvestigationRegistry:
    sandbox: Sandbox
    default_spec: SandboxSpec
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
            session.handle = await self._acquire(investigation_id)
            if self.activity is not None:
                await self.activity.bump(investigation_id)
            session.last_active = _utcnow()
        return session.handle

    async def ensure_handle(self, session: InvestigationSession) -> SandboxHandle:
        # Lock so concurrent callers see a single Sandbox.create — without
        # this, N parallel POSTs to the same investigation would each spin
        # up their own container.
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
                session.handle = await self._acquire(session.investigation_id)
            # Refresh the GLOBAL heartbeat on every wake/use (not just the first)
            # so another pod's idle reaper sees this item as live (#345).
            if self.activity is not None:
                await self.activity.bump(session.investigation_id)
            session.last_active = _utcnow()
        return session.handle

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
            return False
        except SandboxBusy:
            return True  # reachable but slow ⇒ it exists — do not rebuild
        return True

    async def _acquire(self, item: str) -> SandboxHandle:
        """Materialise (or converge on) the item's single live sandbox handle.

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
                    return existing  # a live shared sandbox → converge on ONE
                stale = existing  # dead address → rebuild + swap it out below
        fresh = await self._is_cold(item)
        handle = await self.sandbox.create(self.default_spec, sandbox_id=item)
        # #492: in host-managed mode the host already restored this item's archive
        # into the fresh sandbox during create (and marked it ready), so the app
        # skips its own per-file restore. Otherwise restore from the durable
        # snapshot when the dir was cold, as before.
        if fresh and self.sync is not None and not self.host_managed_durable:
            await self.sync.restore(item, handle)
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
                await self.sandbox.kill(handle)  # lost the race — drop our orphan
                return winner
        return handle

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
                continue
            mirrored.append(inv_id)
        return mirrored

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
                del self._sessions[inv_id]
                killed.append(inv_id)
            except Exception:  # noqa: BLE001 — #366: one bad item must not abort the sweep
                continue
        return killed

    async def enforce_quota(self, max_bytes: int) -> list[str]:
        """#345: recycle any live item whose shared scratch working dir has grown
        past ``max_bytes``, so one runaway workspace can't fill the scratch volume
        the whole fleet shares. 0 ⇒ disabled (the lenient default). Same recycle
        as the idle reaper — mirror→kill→forget — so nothing is lost: the dir is
        written back to the durable snapshot before the rmtree and restored on the
        item's next turn.

        Unlike idle-kill this is NOT gated on global idleness: an over-quota dir
        is reaped even while busy (it's the only relief from disk pressure), and
        because the sweep iterates THIS pod's sessions it naturally targets items
        this pod is serving — the sticky-routed owner is the one that reaps its own
        runaway, and the mirror-before-kill keeps a concurrent pod's view durable."""
        if max_bytes <= 0:
            return []
        recycled: list[str] = []
        for inv_id in list(self._sessions):
            s = self._sessions.get(inv_id)
            if s is None or s.handle is None:
                continue
            try:
                if await self._scratch_usage(s.handle) <= max_bytes:
                    continue
                if self._has_durable:
                    # write-back before rmtree (reconcile — the dir is settled)
                    await self._writeback(inv_id, s.handle, delete=True)
                with contextlib.suppress(SandboxNotFound):  # #366: already-reaped is fine
                    await self.sandbox.kill(s.handle)
                if self.activity is not None:
                    await self.activity.forget(inv_id)
                del self._sessions[inv_id]
                recycled.append(inv_id)
            except Exception:  # noqa: BLE001 — #366: one bad item must not abort the sweep
                continue
        return recycled

    async def _scratch_usage(self, handle: SandboxHandle) -> int:
        """Bytes the item's working dir occupies — the du basis for the scratch
        quota. Summed from the sandbox's own ``walk`` so it works for every
        backend (the shared local dir, mock, http) without a new Protocol method.
        A cold/absent dir reports 0 (nothing to reap)."""
        try:
            entries = await self.sandbox.walk(handle, "/")
        except SandboxNotFound:
            return 0
        return sum(e.size for e in entries)

    async def _globally_idle(self, investigation_id: str, cutoff_ms: int) -> bool:
        """True when no pod has touched the item's shared dir since ``cutoff_ms``.
        No heartbeat wired ⇒ True (single-process: pod-local idleness is global)."""
        if self.activity is None:
            return True
        ms = await self.activity.last_active_ms(investigation_id)
        return ms is None or ms < cutoff_ms

    async def close_all(self) -> None:
        for inv_id in list(self._sessions):
            s = self._sessions.pop(inv_id)
            if s.handle is not None:
                if self._has_durable:
                    await self._writeback(inv_id, s.handle, delete=True)
                await self.sandbox.kill(s.handle)

    async def close_session(self, investigation_id: str) -> None:
        """Manually tear down one investigation's sandbox + remove from
        the registry. Used by the close-investigation API endpoint
        (plan-backend §6)."""
        s = self._sessions.pop(investigation_id, None)
        if s is None:
            return
        if s.handle is not None:
            if self._has_durable:
                await self._writeback(investigation_id, s.handle, delete=True)
            await self.sandbox.kill(s.handle)
            if self.activity is not None:
                await self.activity.forget(investigation_id)
