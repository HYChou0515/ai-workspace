"""KernelService — per-notebook IPython kernel manager.

Each `(investigation_id, notebook_path)` pair gets its own kernel so
that two notebooks in the same investigation have independent
namespaces, and so the agent can run code in `drift.ipynb` while the
user is typing into `pareto.ipynb` without races.

v1 spawns the kernel via `jupyter_client.AsyncKernelManager` directly
on the host (LocalProcessSandbox model). The plan reserves room for
spawning inside the sandbox in v2 — that requires a `kernel_host.py`
shipped in the image — but the service contract here is identical, so
swapping the spawn strategy later is a localised change.

Idle reaping: per-kernel timer (default 30 min after last cell run);
the investigation-level 8h timer is independent and lives in the
InvestigationRegistry.
"""

from __future__ import annotations

import asyncio
import queue as _queue
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..api.events import CellDisplayData, CellDone, CellError, CellEvent, CellStream

if TYPE_CHECKING:
    from jupyter_client import AsyncKernelClient, AsyncKernelManager


@dataclass
class KernelHandle:
    """Pointer to one live kernel — mutable on purpose so restart can
    swap the underlying manager/client without invalidating callers."""

    investigation_id: str
    notebook_path: str
    manager: AsyncKernelManager
    client: AsyncKernelClient
    last_cell_run: datetime = field(default_factory=lambda: datetime.now(UTC))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class KernelService:
    def __init__(self) -> None:
        self._kernels: dict[tuple[str, str], KernelHandle] = {}
        self._lock = asyncio.Lock()

    async def get_or_start(self, investigation_id: str, notebook_path: str) -> KernelHandle:
        key = (investigation_id, notebook_path)
        async with self._lock:
            existing = self._kernels.get(key)
            if existing is not None:
                return existing
            handle = await self._spawn(investigation_id, notebook_path)
            self._kernels[key] = handle
            return handle

    @staticmethod
    async def _spawn(investigation_id: str, notebook_path: str) -> KernelHandle:
        from jupyter_client import AsyncKernelManager

        manager = AsyncKernelManager()
        await manager.start_kernel()
        client = manager.client()
        client.start_channels()
        await client.wait_for_ready(timeout=30)
        return KernelHandle(
            investigation_id=investigation_id,
            notebook_path=notebook_path,
            manager=manager,
            client=client,
        )

    async def execute_cell(self, handle: KernelHandle, code: str) -> AsyncIterator[CellEvent]:
        async with handle.lock:
            handle.last_cell_run = datetime.now(UTC)
            msg_id = handle.client.execute(code)
            async for ev in self._drain_iopub_until_idle(handle.client, msg_id):
                yield ev

    @staticmethod
    async def _drain_iopub_until_idle(
        client: AsyncKernelClient, msg_id: str
    ) -> AsyncIterator[CellEvent]:
        execution_count = 0
        while True:
            try:
                msg = await client.get_iopub_msg(timeout=30)
            except _queue.Empty:  # pragma: no cover — only if kernel hangs
                return
            parent_id = msg.get("parent_header", {}).get("msg_id")
            if parent_id != msg_id:  # pragma: no cover — defensive multi-exec guard
                continue
            mtype = msg["msg_type"]
            content = msg["content"]
            if mtype == "stream":
                yield CellStream(stream=content["name"], text=content["text"])
            elif mtype in ("execute_result", "display_data"):
                data = {k: str(v) for k, v in content.get("data", {}).items()}
                execution_count = content.get("execution_count", execution_count)
                yield CellDisplayData(data=data)
            elif mtype == "error":
                yield CellError(
                    ename=content.get("ename", ""),
                    evalue=content.get("evalue", ""),
                    traceback=list(content.get("traceback", [])),
                )
            elif mtype == "execute_input":
                execution_count = content.get("execution_count", execution_count)
            elif mtype == "status" and content.get("execution_state") == "idle":
                yield CellDone(execution_count=execution_count)
                return

    async def interrupt(self, handle: KernelHandle) -> None:
        await handle.manager.interrupt_kernel()

    async def restart(self, handle: KernelHandle) -> KernelHandle:
        """Replace the underlying kernel; same dict key, same notebook."""
        async with self._lock:
            await self._teardown(handle)
            new = await self._spawn(handle.investigation_id, handle.notebook_path)
            self._kernels[(handle.investigation_id, handle.notebook_path)] = new
            return new

    async def shutdown(self, handle: KernelHandle) -> None:
        async with self._lock:
            await self._teardown(handle)
            self._kernels.pop((handle.investigation_id, handle.notebook_path), None)

    async def shutdown_all(self) -> None:
        async with self._lock:
            for handle in list(self._kernels.values()):
                await self._teardown(handle)
            self._kernels.clear()

    @staticmethod
    async def _teardown(handle: KernelHandle) -> None:
        handle.client.stop_channels()
        await handle.manager.shutdown_kernel(now=True)

    async def reap_idle(self, threshold: timedelta) -> list[tuple[str, str]]:
        """Kill kernels whose last_cell_run is older than `threshold`.
        Returns the (investigation_id, notebook_path) keys that were
        actually reaped — the lifespan loop logs from this."""
        now = datetime.now(UTC)
        async with self._lock:
            stale: list[tuple[tuple[str, str], KernelHandle]] = [
                (k, h) for k, h in self._kernels.items() if (now - h.last_cell_run) > threshold
            ]
            for k, h in stale:
                await self._teardown(h)
                self._kernels.pop(k, None)
            return [k for k, _ in stale]
