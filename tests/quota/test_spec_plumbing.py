"""P2 — the resolved per-App numbers have to REACH the thing that enforces them.

Two seams, tested here because either one alone leaves the knob dead:

1. `SandboxSpec` carries the limits, and `IsolatedProcessSandbox` writes THOSE
   into the item's cgroup instead of the values it was constructed with.
2. `InvestigationRegistry` builds the spec per item (`spec_for`) rather than
   handing every sandbox one constant spec.
"""

from __future__ import annotations

import os

import pytest

from workspace_app.api.registry import InvestigationRegistry
from workspace_app.sandbox.isolated_process import IsolatedProcessSandbox, _CgroupManager
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

# ─── the cgroup honours the spec ───────────────────────────────────────


def test_cgroup_manager_prefers_the_specs_limits(tmp_path):
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cg = mgr.create("h", cpu_cores=2.0, memory_bytes=64 * 1024**2, pids_max=99)
    assert (cg / "memory.max").read_text() == str(64 * 1024**2)
    assert (cg / "cpu.max").read_text() == "200000 100000"
    assert (cg / "pids.max").read_text() == "99"


def test_cgroup_manager_falls_back_per_dimension(tmp_path):
    """A spec that states only memory must keep the deploy's cpu and pids — the
    same per-dimension fall-through the config layer promises."""
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cg = mgr.create("h", memory_bytes=64 * 1024**2)
    assert (cg / "memory.max").read_text() == str(64 * 1024**2)
    assert (cg / "cpu.max").read_text() == "100000 100000"
    assert (cg / "pids.max").read_text() == "256"


def test_cgroup_manager_zero_memory_means_unbounded_not_zero_bytes(tmp_path):
    """0 is this feature's "explicitly unbounded", and it must reach the cgroup
    as `max` — writing a literal 0 would OOM-kill every process instantly. It is
    also NOT the same as `None`: an App that deliberately lifts the memory cap
    must not silently inherit the deploy's."""
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    assert (mgr.create("unbounded", memory_bytes=0) / "memory.max").read_text() == "max"
    assert (mgr.create("inherit", memory_bytes=None) / "memory.max").read_text() == str(
        512 * 1024**2
    )


async def test_isolated_sandbox_applies_the_specs_limits(tmp_path):
    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "sb",
        cgroup_root=tmp_path / "cg",
        uid_base=os.getuid(),
        uid_range=1,  # hash % 1 == 0 ⇒ uid == getuid, so chown is a no-op
        memory_max="64M",
        cpu_cores=0.5,
        pids_max=64,
        acl_runner=lambda argv: None,
        chown_runner=lambda p, u: None,
    )
    h = await sb.create(SandboxSpec(cpu_cores=3.0, memory_bytes=128 * 1024**2), sandbox_id="i-1")
    cg = sb._cgroup_root / h.id
    assert (cg / "memory.max").read_text() == str(128 * 1024**2)
    assert (cg / "cpu.max").read_text() == "300000 100000"
    # pids, unstated by the spec, stays the deploy's.
    assert (cg / "pids.max").read_text() == "64"


# ─── the registry builds the spec per item ─────────────────────────────


class _SpyingSandbox(MockSandbox):
    """Records the spec each `create` was handed, so the test asserts on what
    actually crossed the boundary rather than on what the registry meant to."""

    def __init__(self) -> None:
        super().__init__()
        self.specs: list[tuple[str | None, SandboxSpec]] = []

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.specs.append((sandbox_id, spec))
        return await super().create(spec, sandbox_id)


async def test_registry_asks_for_the_items_own_spec():
    sandbox = _SpyingSandbox()
    registry = InvestigationRegistry(
        sandbox=sandbox,
        spec_for=lambda item: SandboxSpec(cpu_cores=4.0 if item == "big" else 1.0),
    )
    await registry.session("big")
    await registry.ensure_handle(await registry.session("big"))
    assert sandbox.specs[-1][1].cpu_cores == 4.0

    await registry.ensure_handle(await registry.session("small"))
    assert sandbox.specs[-1][1].cpu_cores == 1.0


async def test_registry_defaults_to_a_constant_spec():
    """A registry constructed without `spec_for` (every existing call site, and
    every test that predates this) keeps handing out a bare spec."""
    sandbox = _SpyingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    await registry.ensure_handle(await registry.session("i-1"))
    assert sandbox.specs[-1][1] == SandboxSpec()


@pytest.mark.parametrize("field_", ["cpu_cores", "memory_bytes", "pids_max"])
def test_sandbox_spec_resource_fields_default_to_unstated(field_):
    """`None` ⇒ "the backend's configured default", so every pre-existing caller
    that builds a bare `SandboxSpec()` keeps today's behaviour. Deliberately not
    0 — that is a different answer (explicitly unbounded)."""
    assert getattr(SandboxSpec(), field_) is None
