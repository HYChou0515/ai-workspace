"""A workspace whose sandbox is LIVE, and a count of what reading it costs.

Every operation on a warm workspace resolves where it lives first
(`WorkspaceFiles._warm` → `sandbox.exists(handle, "/")`), and against the hosted
sandbox that resolution is a network round trip. So an operation that reads N
files can cost 2N round trips instead of N+1, which is invisible locally and is
the whole of the latency in production.

What is worth asserting is therefore not a duration but a SHAPE: the cost of
settling where the workspace lives must not scale with how much the workspace
holds. Counting `exists(handle, "/")` at the sandbox boundary is the same seam
`tests/files/test_quota.py` already counts.
"""

from __future__ import annotations

from workspace_app.files.facade import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec


class ProbeCountingSandbox(MockSandbox):
    """Counts the facade's per-operation liveness probe."""

    def __init__(self) -> None:
        super().__init__()
        self.liveness_probes = 0

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        if path == "/":
            self.liveness_probes += 1
        return await super().exists(handle, path)


async def warm_files() -> tuple[WorkspaceFiles, ProbeCountingSandbox]:
    """A facade whose workspace has a live sandbox — the state a real item is in
    while somebody is working in it, and the only state where the probe costs
    anything."""
    sb = ProbeCountingSandbox()
    handle = await sb.create(SandboxSpec())

    async def _resolve(_ws: str) -> SandboxHandle:
        return handle

    return WorkspaceFiles(MemoryFileStore(), sandbox=sb, handle_for=_resolve), sb
