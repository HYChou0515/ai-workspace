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

from ..sandbox.protocol import Sandbox, SandboxHandle, SandboxSpec


class _SyncHook(Protocol):
    """Subset of SandboxSync the registry calls. Lets tests inject a
    recorder without coupling the registry to the concrete SandboxSync.

    Param name stays `workspace_id` because FileStore + SandboxSync are
    domain-agnostic — they treat the identifier as an opaque namespace
    key, regardless of whether the caller calls it 'workspace' or
    'investigation'."""

    async def restore(self, workspace_id: str, handle: SandboxHandle) -> int: ...
    async def reverse(self, workspace_id: str, handle: SandboxHandle) -> int: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class InvestigationSession:
    investigation_id: str
    handle: SandboxHandle | None = None
    last_active: datetime = field(default_factory=_utcnow)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The asyncio.Task driving the in-flight agent turn, if any. Cleared
    # to None when the turn finishes (normally, errored, or cancelled).
    current_turn: asyncio.Task | None = None


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

    async def ensure_handle(self, session: InvestigationSession) -> SandboxHandle:
        # Lock so concurrent callers see a single Sandbox.create — without
        # this, N parallel POSTs to the same investigation would each spin
        # up their own container.
        async with session.lock:
            if session.handle is None:
                session.handle = await self.sandbox.create(self.default_spec)
                # Restore-after-create so the agent's shell starts with
                # the files it left behind last time the investigation was
                # up.
                if self.sync is not None:
                    await self.sync.restore(session.investigation_id, session.handle)
            session.last_active = _utcnow()
        return session.handle

    async def kill_idle(self, threshold: timedelta) -> list[str]:
        cutoff = _utcnow() - threshold
        killed: list[str] = []
        for inv_id in list(self._sessions):
            s = self._sessions[inv_id]
            if s.last_active >= cutoff:
                continue
            if s.handle is not None:
                if self.sync is not None:
                    await self.sync.reverse(inv_id, s.handle)
                await self.sandbox.kill(s.handle)
            del self._sessions[inv_id]
            killed.append(inv_id)
        return killed

    async def close_all(self) -> None:
        for inv_id in list(self._sessions):
            s = self._sessions.pop(inv_id)
            if s.handle is not None:
                if self.sync is not None:
                    await self.sync.reverse(inv_id, s.handle)
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
                await self.sync.reverse(investigation_id, s.handle)
            await self.sandbox.kill(s.handle)
