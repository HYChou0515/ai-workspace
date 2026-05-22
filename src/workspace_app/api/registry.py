"""WorkspaceRegistry — sticky per-workspace state for the API layer.

Why this exists: interrupt (Q10/c3), idle-kill (Q10/b1), and FS↔Sandbox
sync (Q11) all need a single source of truth per workspace — the alive
sandbox handle, the in-flight agent turn, and the last activity
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
    recorder without coupling the registry to the concrete SandboxSync."""

    async def restore(self, workspace_id: str, handle: SandboxHandle) -> int: ...
    async def reverse(self, workspace_id: str, handle: SandboxHandle) -> int: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class WorkspaceSession:
    workspace_id: str
    handle: SandboxHandle | None = None
    last_active: datetime = field(default_factory=_utcnow)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class WorkspaceRegistry:
    sandbox: Sandbox
    default_spec: SandboxSpec
    sync: _SyncHook | None = None
    _sessions: dict[str, WorkspaceSession] = field(default_factory=dict)

    async def session(self, workspace_id: str) -> WorkspaceSession:
        if workspace_id not in self._sessions:
            self._sessions[workspace_id] = WorkspaceSession(workspace_id=workspace_id)
        return self._sessions[workspace_id]

    async def ensure_handle(self, session: WorkspaceSession) -> SandboxHandle:
        # Lock so concurrent callers see a single Sandbox.create — without
        # this, N parallel POSTs to the same workspace would each spin up
        # their own container.
        async with session.lock:
            if session.handle is None:
                session.handle = await self.sandbox.create(self.default_spec)
                # Restore-after-create so the agent's shell starts with
                # the files it left behind last time the workspace was up.
                if self.sync is not None:
                    await self.sync.restore(session.workspace_id, session.handle)
            session.last_active = _utcnow()
        return session.handle

    async def kill_idle(self, threshold: timedelta) -> list[str]:
        cutoff = _utcnow() - threshold
        killed: list[str] = []
        for ws_id in list(self._sessions):
            s = self._sessions[ws_id]
            if s.last_active >= cutoff:
                continue
            if s.handle is not None:
                if self.sync is not None:
                    await self.sync.reverse(ws_id, s.handle)
                await self.sandbox.kill(s.handle)
            del self._sessions[ws_id]
            killed.append(ws_id)
        return killed

    async def close_all(self) -> None:
        for ws_id in list(self._sessions):
            s = self._sessions.pop(ws_id)
            if s.handle is not None:
                if self.sync is not None:
                    await self.sync.reverse(ws_id, s.handle)
                await self.sandbox.kill(s.handle)
