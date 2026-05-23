"""Phase 8 — KernelService end-to-end against a real ipykernel.

These are integration tests against jupyter_client + ipykernel: they
spawn an actual Python kernel as a subprocess on the host. Each test
costs ~1-2s of kernel-spawn latency; the suite is scoped tightly to
the contract (events surfaced, handle reuse, restart/shutdown).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from workspace_app.api.events import CellDisplayData, CellDone, CellError, CellStream
from workspace_app.kernels import KernelService


@pytest.fixture
async def service():
    s = KernelService()
    try:
        yield s
    finally:
        await s.shutdown_all()


async def test_execute_print_emits_stdout_stream(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    events = [e async for e in service.execute_cell(h, "print('hi')")]
    streams = [e for e in events if isinstance(e, CellStream)]
    assert any(e.stream == "stdout" and "hi" in e.text for e in streams)


async def test_execute_terminates_with_cell_done(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    events = [e async for e in service.execute_cell(h, "print('x')")]
    done = [e for e in events if isinstance(e, CellDone)]
    assert len(done) == 1
    assert done[0].execution_count >= 1


async def test_expression_emits_display_data(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    events = [e async for e in service.execute_cell(h, "1 + 1")]
    displays = [e for e in events if isinstance(e, CellDisplayData)]
    assert any("2" in (e.data.get("text/plain") or "") for e in displays)


async def test_error_emits_cell_error(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    events = [e async for e in service.execute_cell(h, "raise ValueError('boom')")]
    errs = [e for e in events if isinstance(e, CellError)]
    assert len(errs) == 1
    assert errs[0].ename == "ValueError"
    assert "boom" in errs[0].evalue


async def test_get_or_start_reuses_kernel_for_same_notebook(service: KernelService):
    h1 = await service.get_or_start("inv-1", "nb.ipynb")
    h2 = await service.get_or_start("inv-1", "nb.ipynb")
    assert h1 is h2


async def test_get_or_start_distinct_notebook_distinct_kernel(service: KernelService):
    h1 = await service.get_or_start("inv-1", "a.ipynb")
    h2 = await service.get_or_start("inv-1", "b.ipynb")
    assert h1 is not h2


async def test_get_or_start_distinct_investigation_distinct_kernel(service: KernelService):
    h1 = await service.get_or_start("inv-1", "nb.ipynb")
    h2 = await service.get_or_start("inv-2", "nb.ipynb")
    assert h1 is not h2


async def test_state_persists_across_cells_in_same_kernel(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    [e async for e in service.execute_cell(h, "x = 42")]
    events = [e async for e in service.execute_cell(h, "print(x)")]
    streams = [e for e in events if isinstance(e, CellStream)]
    assert any("42" in e.text for e in streams)


async def test_restart_clears_kernel_state(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    [e async for e in service.execute_cell(h, "x = 42")]
    h2 = await service.restart(h)
    # Same handle conceptually (same notebook) — the service replaces
    # the underlying kernel but keeps the (inv, path) mapping.
    events = [e async for e in service.execute_cell(h2, "print(x)")]
    errs = [e for e in events if isinstance(e, CellError)]
    assert any(e.ename == "NameError" for e in errs)


async def test_shutdown_removes_handle(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    await service.shutdown(h)
    # A fresh get_or_start gives a new kernel.
    h2 = await service.get_or_start("inv-1", "nb.ipynb")
    assert h is not h2


async def test_reap_idle_kills_kernels_past_threshold(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    [e async for e in service.execute_cell(h, "1")]
    # Force the kernel's last_cell_run back in time.
    import datetime as _dt

    h.last_cell_run = _dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=31)
    killed = await service.reap_idle(timedelta(minutes=30))
    assert ("inv-1", "nb.ipynb") in killed


async def test_interrupt_smoke(service: KernelService):
    """interrupt() just delegates to manager.interrupt_kernel — smoke
    test that it doesn't raise. Testing the actual cancellation of a
    busy cell would require a long-running cell + race timing; out of
    scope for the unit-level contract."""
    h = await service.get_or_start("inv-1", "nb.ipynb")
    await service.interrupt(h)


async def test_reap_idle_keeps_recently_active_kernels(service: KernelService):
    h = await service.get_or_start("inv-1", "nb.ipynb")
    [e async for e in service.execute_cell(h, "1")]
    killed = await service.reap_idle(timedelta(minutes=30))
    assert killed == []
    # Same handle still alive.
    h2 = await service.get_or_start("inv-1", "nb.ipynb")
    assert h is h2
