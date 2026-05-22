from __future__ import annotations

from dataclasses import dataclass, field

from ..filestore.protocol import FileStore
from ..sandbox.protocol import Sandbox, SandboxHandle, SandboxSpec
from ..sync import SandboxSync


@dataclass
class AgentToolContext:
    """Per-run context passed into agent tools.

    Sandbox is created lazily on first `exec` call (Q10 a2+ policy):
    pure file ops (read/write/ls/exists/delete) never spin one up,
    so a chat that doesn't run shell commands stays free.

    `sync` bridges FileStore (durable, agent's file tools target it) and
    Sandbox (ephemeral, exec runs there): exec_impl calls sync.flush
    before each shell so the sandbox sees writes the agent just made.
    """

    workspace_id: str
    sandbox: Sandbox
    filestore: FileStore
    sync: SandboxSync
    sandbox_spec: SandboxSpec = field(default_factory=SandboxSpec)
    handle: SandboxHandle | None = None

    async def ensure_sandbox(self) -> SandboxHandle:
        if self.handle is None:
            self.handle = await self.sandbox.create(self.sandbox_spec)
        return self.handle
