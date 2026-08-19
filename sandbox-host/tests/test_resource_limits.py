"""P3 (host half) — per-sandbox resource ceilings arrive over the wire.

Two paths, because either alone is a lie:

* a create that STATES limits gets those limits in its cgroup;
* a create that states nothing (an older app, or an item whose App declares
  nothing) gets this host's configured `SANDBOX_HOST_*` defaults.

The two are deliberately the same case: an absent field and an explicit null
both mean "not stated", so neither side has to be deployed first.
"""

from __future__ import annotations

from sandbox_host.isolated_process import _CgroupManager, _cpu_max, _fmt_bytes
from sandbox_host.protocol import EnforcedLimits, SandboxSpec


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


# ── what this host ADVERTISES, and why it is asserted against the files ──


def test_what_the_host_publishes_is_what_it_writes_to_the_cgroup(tmp_path):
    """`/healthz` publishes these numbers and the app CHARGES a person for them,
    so they have to be the ceilings the cgroup really gets — not a second
    calculation that happens to agree today.

    Asserted against the files `create` writes, over every combination of stated
    and unstated, because that is the only version of this test a wrong
    `effective()` cannot pass. Returning a constant satisfies "it returns
    something"; it cannot satisfy "it equals `cpu.max`"."""
    mgr = _CgroupManager(tmp_path, memory_max="512M", cpu_cores=1.0, pids_max=256)
    cases = [
        (None, None),  # an App that declares nothing — the case that was charged 0
        (2.0, 64 * 1024**2),  # both stated
        (None, 64 * 1024**2),  # memory only: cpu must keep the host default
        (2.0, None),  # cpu only
        (2.0, 0),  # 0 = explicitly unbounded, not "unstated"
    ]
    for i, (cpu, mem) in enumerate(cases):
        cg = mgr.create(f"h{i}", cpu_cores=cpu, memory_bytes=mem)
        enforced = mgr.effective(cpu, mem)
        assert enforced.cpu_cores is not None
        assert enforced.memory_bytes is not None
        assert (cg / "cpu.max").read_text() == _cpu_max(enforced.cpu_cores), (cpu, mem)
        assert (cg / "memory.max").read_text() == _fmt_bytes(enforced.memory_bytes), (cpu, mem)


async def test_the_sandbox_reports_its_own_cgroup_managers_answer(tmp_path):
    """`IsolatedProcessSandbox.effective_limits` is what `/healthz` calls. It must
    not compute its own version of the fall-through."""
    from sandbox_host.isolated_process import IsolatedProcessSandbox

    sb = IsolatedProcessSandbox(
        root_dir=tmp_path / "root",
        cgroup_root=tmp_path / "cg",
        uid_min=100000,
        uid_max=100010,
        memory_max="256M",
        cpu_cores=0.5,
        pids_max=64,
    )
    assert await sb.effective_limits(SandboxSpec()) == sb._cgroups.effective(None, None)
    assert (await sb.effective_limits(SandboxSpec())).cpu_cores == 0.5
    assert (await sb.effective_limits(SandboxSpec())).memory_bytes == 256 * 1024**2
    # a stated ceiling wins, per dimension
    stated = await sb.effective_limits(SandboxSpec(cpu_cores=3.0))
    assert (stated.cpu_cores, stated.memory_bytes) == (3.0, 256 * 1024**2)


async def test_a_backend_with_no_cgroups_publishes_nothing_rather_than_a_guess(tmp_path):
    """The plain local process sandbox applies no cgroup at all. Publishing the
    request back would let the app charge a person for a ceiling nothing
    enforces."""
    from sandbox_host.local_process import LocalProcessSandbox

    sb = LocalProcessSandbox(root_dir=tmp_path)
    assert await sb.effective_limits(SandboxSpec()) == EnforcedLimits(None, None)
