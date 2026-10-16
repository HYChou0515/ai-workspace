"""A sandbox's measured use (docs/plan-capacity-usage.md §5.2 `sandbox`, §6.5):
read from its cgroup, integrated between samples, and split across the
windows each interval spans — on a fake cgroup tree, the same plain files the
kernel serves."""

from __future__ import annotations

from pathlib import Path

from workspace_app.capacity.cgroup_usage import Target, UsageSampler, read_cgroup

W0 = 1_760_501_100.0  # a multiple of 300 s


def _cgroup(
    root: Path,
    name: str,
    *,
    cpu_us: int,
    mem: int,
    periods: int = 0,
    throttled_n: int = 0,
    throttled_us: int = 0,
    oom: int = 0,
    cpu_psi_us: int | None = 0,
    mem_psi_us: int | None = 0,
    cpu_max: str = "100000 100000",
    mem_max: str = "536870912",
) -> Path:
    cg = root / name
    cg.mkdir(parents=True, exist_ok=True)
    (cg / "cpu.stat").write_text(
        f"usage_usec {cpu_us}\nuser_usec 0\nsystem_usec 0\n"
        f"nr_periods {periods}\nnr_throttled {throttled_n}\nthrottled_usec {throttled_us}\n"
    )
    (cg / "memory.current").write_text(f"{mem}\n")
    (cg / "memory.events").write_text(f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom}\n")
    for name_, total in (("cpu.pressure", cpu_psi_us), ("memory.pressure", mem_psi_us)):
        path = cg / name_
        if total is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(
                f"some avg10=0.00 avg60=0.00 avg300=0.00 total={total}\n"
                "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            )
    (cg / "cpu.max").write_text(f"{cpu_max}\n")
    (cg / "memory.max").write_text(f"{mem_max}\n")
    return cg


def test_a_cgroup_reads_as_the_kernel_writes_it(tmp_path):
    cg = _cgroup(
        tmp_path,
        "s1",
        cpu_us=2_500_000,
        mem=1024,
        periods=10,
        throttled_n=3,
        throttled_us=400_000,
        oom=1,
        cpu_psi_us=7_000,
        cpu_max="50000 100000",
        mem_max="max",
    )

    r = read_cgroup(cg, 5.0)

    assert r is not None
    assert (r.cpu_us, r.periods, r.throttled_n, r.throttled_us) == (2_500_000, 10, 3, 400_000)
    assert (r.mem_bytes, r.oom_kills, r.cpu_psi_us) == (1024, 1, 7_000)
    assert r.cpu_limit == 0.5
    assert r.mem_limit is None, "max is no limit"


def test_a_cgroup_that_is_gone_reads_as_nothing(tmp_path):
    assert read_cgroup(tmp_path / "reaped", 0.0) is None


def test_a_kernel_without_psi_reads_as_unknown_not_zero(tmp_path):
    cg = _cgroup(tmp_path, "s1", cpu_us=0, mem=0, cpu_psi_us=None, mem_psi_us=None)

    r = read_cgroup(cg, 0.0)

    assert r is not None and r.cpu_psi_us is None and r.mem_psi_us is None


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _sampler(tmp_path: Path, clock: _Clock, targets: list[Target], out: list[dict]) -> UsageSampler:
    host = tmp_path / "host-pressure"
    host.mkdir(exist_ok=True)
    for name in ("cpu", "memory"):
        (host / name).write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    return UsageSampler(
        tmp_path / "cg",
        window_s=300,
        pod="sh-1",
        component="sandbox-host",
        targets=lambda: list(targets),
        write=out.extend,
        clock=clock,
        host_pressure=host,
    )


def _rows(out: list[dict], kind: str = "sandbox") -> list[dict]:
    return [d["usage"] | {"@": d["@timestamp"][11:16]} for d in out if d["usage"]["kind"] == kind]


def test_use_between_samples_is_split_across_the_windows_it_spans(tmp_path):
    clock, out = _Clock(W0 + 240), []
    target = Target(sandbox="s1", item="i1", user="alice", app="pm")
    root = tmp_path / "cg"
    _cgroup(root, "s1", cpu_us=0, mem=100)
    sampler = _sampler(tmp_path, clock, [target], out)
    sampler.sample()  # 04:09:00 — the first pass is a baseline
    _cgroup(root, "s1", cpu_us=120_000_000, mem=300, throttled_us=6_000_000, cpu_psi_us=60_000)
    clock.t = W0 + 360  # 04:11:00, one minute into the next window
    sampler.sample()
    clock.t = W0 + 600  # 04:15:00 — the second window has closed
    sampler.sample()
    sampler.close()

    rows = {r["@"]: r for r in _rows(out)}
    # 120 s of CPU over 120 s, 60 s in each window.
    assert rows["04:05"]["cpu_s"] == 60.0 and rows["04:10"]["cpu_s"] == 60.0
    assert rows["04:05"]["thr_s"] == 3.0
    assert rows["04:05"]["cpu_psi_s"] == 0.03
    # Memory rises 100 → 300 over the interval: trapezoids of each half.
    assert rows["04:05"]["mem_bs"] == 60 * 150
    assert rows["04:05"]["mem_max"] == 200
    assert (rows["04:05"]["user"], rows["04:05"]["app"], rows["04:05"]["item"]) == (
        "alice",
        "pm",
        "i1",
    )
    assert rows["04:05"]["cpu_limit"] == 1.0
    assert rows["04:05"]["window_s"] == 300
    hosts = {r["@"]: r for r in _rows(out, "sandbox_host")}
    assert hosts["04:05"]["live_s"] == 60.0


def test_a_counter_that_went_backwards_is_a_new_cgroup_counted_from_zero(tmp_path):
    clock, out = _Clock(W0 + 60), []
    root = tmp_path / "cg"
    _cgroup(root, "s1", cpu_us=500_000_000, mem=0)
    sampler = _sampler(tmp_path, clock, [Target("s1", "i1", "alice", "pm")], out)
    sampler.sample()
    _cgroup(root, "s1", cpu_us=2_000_000, mem=0)  # recreated: 2 s since
    clock.t = W0 + 120
    sampler.sample()
    sampler.close()

    assert [r["cpu_s"] for r in _rows(out)] == [2.0]


def test_a_sandbox_born_after_the_first_pass_is_counted_from_zero(tmp_path):
    clock, out = _Clock(W0 + 60), []
    targets: list[Target] = []
    root = tmp_path / "cg"
    sampler = _sampler(tmp_path, clock, targets, out)
    sampler.sample()  # nothing running yet
    _cgroup(root, "s2", cpu_us=30_000_000, mem=0)
    targets.append(Target("s2", "i2", "bob", ""))
    clock.t = W0 + 120
    sampler.sample()
    sampler.close()

    assert [(r["sandbox"], r["cpu_s"]) for r in _rows(out)] == [("s2", 30.0)]


def test_a_sandbox_present_at_the_first_pass_is_only_a_baseline(tmp_path):
    """The host restarted under running sandboxes: their counters hold hours of
    use from before, which belong to no window this host saw."""
    clock, out = _Clock(W0 + 60), []
    root = tmp_path / "cg"
    _cgroup(root, "s1", cpu_us=9_000_000_000, mem=0)
    sampler = _sampler(tmp_path, clock, [Target("s1", "i1", "alice", "pm")], out)
    sampler.sample()
    sampler.close()

    assert _rows(out) == []
