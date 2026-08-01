"""P3 (host half) — per-sandbox resource ceilings arrive over the wire.

Two paths, because either alone is a lie:

* a create that STATES limits gets those limits in its cgroup;
* a create that states nothing (an older app, or an item whose App declares
  nothing) gets this host's configured `SANDBOX_HOST_*` defaults.

The two are deliberately the same case: an absent field and an explicit null
both mean "not stated", so neither side has to be deployed first.
"""

from __future__ import annotations

from sandbox_host.isolated_process import _CgroupManager
from sandbox_host.protocol import SandboxSpec


def test_a_create_that_states_limits_gets_them(tmp_path):
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cg = mgr.create("h", cpu_cores=2.0, memory_bytes=64 * 1024**2, pids_max=99)
    assert (cg / "memory.max").read_text() == str(64 * 1024**2)
    assert (cg / "cpu.max").read_text() == "200000 100000"
    assert (cg / "pids.max").read_text() == "99"


def test_a_create_that_states_nothing_gets_this_hosts_defaults(tmp_path):
    """What an older app sends, and what an App declaring nothing resolves to."""
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cg = mgr.create("h")
    assert (cg / "memory.max").read_text() == str(512 * 1024**2)
    assert (cg / "cpu.max").read_text() == "100000 100000"
    assert (cg / "pids.max").read_text() == "256"


def test_limits_fall_back_per_dimension(tmp_path):
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cg = mgr.create("h", memory_bytes=64 * 1024**2)
    assert (cg / "memory.max").read_text() == str(64 * 1024**2)
    assert (cg / "cpu.max").read_text() == "100000 100000"  # host default kept


def test_zero_memory_means_unbounded_not_zero_bytes(tmp_path):
    """0 is "no limit" throughout this feature. Writing a literal 0 into
    `memory.max` would OOM-kill every process in the sandbox instantly."""
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    assert (mgr.create("h", memory_bytes=0) / "memory.max").read_text() == "max"


def test_the_spec_defaults_to_unstated():
    spec = SandboxSpec()
    assert spec.cpu_cores is None
    assert spec.memory_bytes is None
    assert spec.pids_max is None
