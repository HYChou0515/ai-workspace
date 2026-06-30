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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..sandbox.protocol import Sandbox, SandboxHandle, SandboxNotFound, SandboxSpec


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
    _sessions: dict[str, InvestigationSession] = field(default_factory=dict)

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

    def peek_handle(self, investigation_id: str) -> SandboxHandle | None:
        """The handle WorkspaceFiles routes a file op through — WITHOUT creating
        a sandbox. This pod's session handle when it owns one; otherwise the
        shared-vol handle derived from the id, so a read on ANY pod hits the
        live shared dir (the facade falls back to the snapshot when that dir is
        cold) instead of a stale snapshot (#345 — correctness no longer depends
        on sticky routing). None when the backend isn't id-addressable and this
        pod has no session."""
        s = self._sessions.get(investigation_id)
        if s is not None and s.handle is not None:
            return s.handle
        return self._handle_for_id(investigation_id)

    async def ensure_handle(self, session: InvestigationSession) -> SandboxHandle:
        # Lock so concurrent callers see a single Sandbox.create — without
        # this, N parallel POSTs to the same investigation would each spin
        # up their own container.
        async with session.lock:
            if session.handle is None:
                item = session.investigation_id
                # #345 restore-when-absent: a shared-vol item dir may already be
                # live (this pod cold-started, or another pod materialized it).
                # Probe BEFORE create so we restore from the snapshot ONLY when
                # the dir doesn't exist — re-restoring over a live dir would
                # resurrect files the agent deleted. A backend that mints its own
                # handles (handle_for_id None, e.g. HTTP) is always a fresh
                # create, so it always restores (the prior per-pod behaviour).
                fresh = await self._is_cold(item)
                session.handle = await self.sandbox.create(self.default_spec, sandbox_id=item)
                if fresh and self.sync is not None:
                    await self.sync.restore(item, session.handle)
            session.last_active = _utcnow()
        return session.handle

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
        if s is None or s.handle is None or self.sync is None:
            return
        await self.sync.mirror(investigation_id, s.handle)

    async def mirror_warm(self) -> list[str]:
        """Throttle sweep: mirror every warm session to the snapshot via a
        version-diff (cheap when nothing changed — only changed files are
        downloaded). Run periodically (≤window) so a crash loses at most a
        window of work, and so files the shell created — which the file tools
        never see — still get persisted."""
        mirrored: list[str] = []
        for inv_id in list(self._sessions):
            s = self._sessions.get(inv_id)
            if s is None or s.handle is None or self.sync is None:
                continue
            await self.sync.mirror(inv_id, s.handle)
            mirrored.append(inv_id)
        return mirrored

    async def kill_idle(self, threshold: timedelta) -> list[str]:
        cutoff = _utcnow() - threshold
        killed: list[str] = []
        for inv_id in list(self._sessions):
            s = self._sessions[inv_id]
            if s.last_active >= cutoff:
                continue
            if s.handle is not None:
                if self.sync is not None:
                    await self.sync.mirror(inv_id, s.handle)
                await self.sandbox.kill(s.handle)
            del self._sessions[inv_id]
            killed.append(inv_id)
        return killed

    async def close_all(self) -> None:
        for inv_id in list(self._sessions):
            s = self._sessions.pop(inv_id)
            if s.handle is not None:
                if self.sync is not None:
                    await self.sync.mirror(inv_id, s.handle)
                await self.sandbox.kill(s.handle)

    async def close_session(self, investigation_id: str) -> None:
        """Manually tear down one investigation's sandbox + remove from
        the registry. Used by the close-investigation API endpoint
        (plan-backend §6)."""
        s = self._sessions.pop(investigation_id, None)
        if s is None:
            return
        if s.handle is not None:
            if self.sync is not None:
                await self.sync.mirror(investigation_id, s.handle)
            await self.sandbox.kill(s.handle)
