"""A sandbox's measured use, from its cgroup (docs/plan-capacity-usage.md §6.5).

`read_cgroup` reads what the kernel keeps for one cgroup v2 directory.
`UsageSampler` reads every live sandbox on a timer and integrates successive
readings into `sandbox` rows — CPU seconds, memory × seconds, throttling,
pressure, OOM kills — splitting each interval across the windows it spans, so
a window's row describes that window and no other. One `sandbox_host` row per
window carries the host's own pressure and the sandbox-seconds alive in it
(how many sandboxes, times how long).

Throttling and pressure answer different questions (E15): throttled time is a
sandbox held at its OWN `cpu.max`; pressure is time it waited for a CPU the
machine did not have. Only the second says "buy machines".

ONE implementation, kept byte for byte identical with sandbox-host's copy
(`sandbox-host/src/sandbox_host/cgroup_usage.py`; `tests/capacity/test_copies.py`
compares the bytes): sandbox-host samples its own sandboxes, and the app
samples its own under `kind: local` with isolation. Standard library only.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .usage_wire import document, window_start

#: Where the kernel publishes the whole machine's pressure.
HOST_PRESSURE = Path("/proc/pressure")


@dataclass(frozen=True)
class Reading:
    """One cgroup at one moment. A field the kernel did not offer is None."""

    at_s: float
    cpu_us: int | None
    periods: int | None
    throttled_n: int | None
    throttled_us: int | None
    mem_bytes: int | None
    oom_kills: int | None
    cpu_psi_us: int | None
    mem_psi_us: int | None
    cpu_limit: float | None
    mem_limit: int | None


@dataclass(frozen=True)
class Target:
    """A live sandbox: its cgroup's name, and whose it is."""

    sandbox: str
    item: str
    user: str
    app: str


def _keyed(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    with contextlib.suppress(OSError):
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("-").isdigit():
                out[parts[0]] = int(parts[1])
    return out


def _single(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_pressure(path: Path) -> int | None:
    """The `some` line's `total=` (microseconds stalled), or None without PSI."""
    text = _single(path)
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("some "):
            for part in line.split():
                if part.startswith("total="):
                    return int(part[len("total=") :])
    return None


def _cpu_limit(text: str | None) -> float | None:
    if not text:
        return None
    quota, _, period = text.partition(" ")
    if quota == "max" or not period:
        return None
    return int(quota) / int(period)


def _mem_limit(text: str | None) -> int | None:
    if not text or text == "max":
        return None
    return int(text)


def read_cgroup(path: Path, at_s: float) -> Reading | None:
    """One cgroup's counters at `at_s`; None when the cgroup is gone."""
    if not path.is_dir():
        return None
    stat = _keyed(path / "cpu.stat")
    events = _keyed(path / "memory.events")
    mem = _single(path / "memory.current")
    return Reading(
        at_s=at_s,
        cpu_us=stat.get("usage_usec"),
        periods=stat.get("nr_periods"),
        throttled_n=stat.get("nr_throttled"),
        throttled_us=stat.get("throttled_usec"),
        mem_bytes=int(mem) if mem and mem.isdigit() else None,
        oom_kills=events.get("oom_kill"),
        cpu_psi_us=read_pressure(path / "cpu.pressure"),
        mem_psi_us=read_pressure(path / "memory.pressure"),
        cpu_limit=_cpu_limit(_single(path / "cpu.max")),
        mem_limit=_mem_limit(_single(path / "memory.max")),
    )


def _zero(at_s: float, like: Reading) -> Reading:
    """A cgroup the moment it was made: every counter at zero."""
    return Reading(at_s, 0, 0, 0, 0, 0, 0, 0, 0, like.cpu_limit, like.mem_limit)


def _delta(before: int | None, after: int | None) -> int | None:
    """How far a counter moved; a counter that went backwards is a new cgroup
    counting from zero."""
    if before is None or after is None:
        return None
    return after if after < before else after - before


@dataclass
class _Acc:
    target: Target
    cpu_s: float = 0.0
    mem_bs: float = 0.0
    mem_max: float = 0.0
    thr_s: float = 0.0
    thr_n: float = 0.0
    periods: float = 0.0
    cpu_psi_s: float | None = None
    mem_psi_s: float | None = None
    oom: float = 0.0
    live_s: float = 0.0
    cpu_limit: float | None = None
    mem_limit: int | None = None


@dataclass
class _HostAcc:
    live_s: float = 0.0
    cpu_psi_s: float | None = None
    mem_psi_s: float | None = None


def _plus(total: float | None, more: float | None) -> float | None:
    if more is None:
        return total
    return more if total is None else total + more


@dataclass
class UsageSampler:
    """Samples every live sandbox's cgroup; writes a window's rows once it closes.

    The first pass only takes a baseline: a sandbox already running when the
    sampler starts holds counters from before, which belong to no window this
    sampler saw. A sandbox first seen on a later pass was born since the last
    one, so its counters are counted from zero."""

    cgroup_root: Path
    window_s: int
    pod: str
    component: str
    targets: Callable[[], Iterable[Target]]
    write: Callable[[list[dict[str, Any]]], None]
    clock: Callable[[], float]
    host_pressure: Path = HOST_PRESSURE
    _prev: dict[str, Reading] = field(default_factory=dict)
    _windows: dict[int, dict[str, _Acc]] = field(default_factory=dict)
    _hosts: dict[int, _HostAcc] = field(default_factory=dict)
    _host_prev: tuple[float, int | None, int | None] | None = None
    _last_at: float | None = None

    def sample(self) -> None:
        now = self.clock()
        first_pass = self._last_at is None
        seen: set[str] = set()
        for target in self.targets():
            reading = read_cgroup(self.cgroup_root / target.sandbox, now)
            if reading is None:
                continue
            seen.add(target.sandbox)
            prev = self._prev.get(target.sandbox)
            if prev is None and not first_pass and self._last_at is not None:
                prev = _zero(self._last_at, reading)
            if prev is not None:
                self._charge(target, prev, reading)
            self._prev[target.sandbox] = reading
        for gone in set(self._prev) - seen:
            del self._prev[gone]
        self._sample_host(now)
        self._last_at = now
        self._emit(lambda start: start + self.window_s * 1000 <= now * 1000)

    def close(self) -> None:
        """Write every window that has anything in it, the open one included."""
        self._emit(lambda _start: True)

    # ── internals ─────────────────────────────────────────────────────────
    def _spans(self, t0: float, t1: float) -> Iterator[tuple[int, float, float]]:
        start = t0
        while start < t1:
            window = window_start(int(start * 1000), self.window_s)
            end = min(t1, window / 1000 + self.window_s)
            yield window, start, end
            start = end

    def _charge(self, target: Target, prev: Reading, cur: Reading) -> None:
        dt = cur.at_s - prev.at_s
        if dt <= 0:
            return
        cpu = _delta(prev.cpu_us, cur.cpu_us)
        thr = _delta(prev.throttled_us, cur.throttled_us)
        thr_n = _delta(prev.throttled_n, cur.throttled_n)
        periods = _delta(prev.periods, cur.periods)
        cpu_psi = _delta(prev.cpu_psi_us, cur.cpu_psi_us)
        mem_psi = _delta(prev.mem_psi_us, cur.mem_psi_us)
        oom = _delta(prev.oom_kills, cur.oom_kills)
        for window, w0, w1 in self._spans(prev.at_s, cur.at_s):
            share = (w1 - w0) / dt
            acc = self._windows.setdefault(window, {}).get(target.sandbox)
            if acc is None:
                acc = self._windows[window][target.sandbox] = _Acc(target)
            acc.cpu_s += (cpu or 0) * share / 1e6
            acc.thr_s += (thr or 0) * share / 1e6
            acc.thr_n += (thr_n or 0) * share
            acc.periods += (periods or 0) * share
            acc.oom += (oom or 0) * share
            acc.cpu_psi_s = _plus(acc.cpu_psi_s, None if cpu_psi is None else cpu_psi * share / 1e6)
            acc.mem_psi_s = _plus(acc.mem_psi_s, None if mem_psi is None else mem_psi * share / 1e6)
            if prev.mem_bytes is not None and cur.mem_bytes is not None:
                slope = (cur.mem_bytes - prev.mem_bytes) / dt
                m0 = prev.mem_bytes + slope * (w0 - prev.at_s)
                m1 = prev.mem_bytes + slope * (w1 - prev.at_s)
                acc.mem_bs += (m0 + m1) / 2 * (w1 - w0)
                acc.mem_max = max(acc.mem_max, m0, m1)
            acc.live_s += w1 - w0
            acc.cpu_limit, acc.mem_limit = cur.cpu_limit, cur.mem_limit

    def _sample_host(self, now: float) -> None:
        cpu = read_pressure(self.host_pressure / "cpu")
        mem = read_pressure(self.host_pressure / "memory")
        prev, self._host_prev = self._host_prev, (now, cpu, mem)
        if prev is None or now <= prev[0]:
            return
        dt = now - prev[0]
        d_cpu, d_mem = _delta(prev[1], cpu), _delta(prev[2], mem)
        for window, w0, w1 in self._spans(prev[0], now):
            share = (w1 - w0) / dt
            host = self._hosts.setdefault(window, _HostAcc())
            host.cpu_psi_s = _plus(host.cpu_psi_s, None if d_cpu is None else d_cpu * share / 1e6)
            host.mem_psi_s = _plus(host.mem_psi_s, None if d_mem is None else d_mem * share / 1e6)

    def _emit(self, due: Callable[[int], bool]) -> None:
        docs: list[dict[str, Any]] = []
        for window in sorted(set(self._windows) | set(self._hosts)):
            if not due(window):
                continue
            accs = self._windows.pop(window, {})
            host = self._hosts.pop(window, _HostAcc())
            for sandbox, acc in accs.items():
                docs.append(
                    self._doc("sandbox", window, _sandbox_fields(sandbox, acc, self.window_s))
                )
            live = sum(acc.live_s for acc in accs.values())
            docs.append(
                self._doc(
                    "sandbox_host",
                    window,
                    {
                        "live_s": round(live, 6),
                        "cpu_psi_s": _round(host.cpu_psi_s),
                        "mem_psi_s": _round(host.mem_psi_s),
                        "window_s": self.window_s,
                    },
                )
            )
        if docs:
            self.write(docs)

    def _doc(self, kind: str, window: int, fields: dict[str, Any]) -> dict[str, Any]:
        return document(kind, window, pod=self.pod, component=self.component, fields=fields)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _sandbox_fields(sandbox: str, acc: _Acc, window_s: int) -> dict[str, Any]:
    t = acc.target
    return {
        "item": t.item,
        "user": t.user or "unknown",
        "app": t.app,
        "sandbox": sandbox,
        "cpu_s": round(acc.cpu_s, 6),
        "mem_bs": round(acc.mem_bs, 3),
        "mem_max": int(acc.mem_max),
        "thr_s": round(acc.thr_s, 6),
        "thr_n": round(acc.thr_n, 3),
        "periods": round(acc.periods, 3),
        "cpu_psi_s": _round(acc.cpu_psi_s),
        "mem_psi_s": _round(acc.mem_psi_s),
        "oom": round(acc.oom, 3),
        "cpu_limit": acc.cpu_limit,
        "mem_limit": acc.mem_limit,
        "window_s": window_s,
    }
