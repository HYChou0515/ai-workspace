"""Check the operator's four sources against their contract (docs/plan-capacity-usage.md §8).

    uv run python -m workspace_app.capacity check [--hours 24] [--config config.yaml]

Calls every method of every configured source over the last N hours and
reports what the dashboard would otherwise draw wrong without a word:
buckets not aligned to the slot (every source), components the platform does
not know, negative or impossible values, sandbox rows whose window cannot be
placed in the slots, a kernel without CPU pressure (PSI), LiteLLM rows
without this platform's tags or with tags that do not parse, and gaps in the
sources sampled every slot. Exit 1 when anything is an error.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .analysis.model import find_gaps, gap_runs
from .analysis.observe import parse_tags
from .analysis.period import Period
from .analysis.thresholds import same_model
from .service import long_step
from .sources import COMPONENTS, AggregateQuery, CapacitySources

#: The keys a platform tag (`ws.<key>:<value>`) may carry (§7).
TAG_KEYS = ("user", "app", "purpose", "call")

WORK_KINDS = ("req:", "stream:", "turn:", "turn-events:", "job:")


@dataclass(frozen=True)
class Finding:
    source: str
    level: str  # error | warning | info
    message: str


def _aligned(t_ms: int, step_ms: int) -> bool:
    return t_ms % step_ms == 0


def _check(name: str, findings: list[Finding], run: Callable[[], None]) -> None:
    try:
        run()
    except Exception as exc:  # noqa: BLE001 — the report is the point
        findings.append(Finding(name, "error", f"raised {type(exc).__name__}: {exc}"))


def check_sources(
    sources: CapacitySources, *, now_ms: int, hours: float, step_s: int
) -> list[Finding]:
    step_ms = step_s * 1000
    end = now_ms // step_ms * step_ms
    start = end - int(hours * 3_600_000) // step_ms * step_ms
    out: list[Finding] = []

    def say(source: str, level: str, message: str) -> None:
        out.append(Finding(source, level, message))

    def unaligned(source: str, stamps: list[int]) -> None:
        for t in stamps:
            if not _aligned(t, step_ms):
                say(source, "error", f"bucket {t} not aligned to {step_s}s")
                return

    def gaps(source: str, stamps: list[int]) -> None:
        period = Period(start, end, step_s, ZoneInfo("UTC"))
        present = np.zeros(period.n, dtype=bool)
        for t in stamps:
            i = period.index(t)
            if i is not None:
                present[i] = True
        runs = gap_runs(present)
        if runs:
            hours = sum(b - a for a, b in runs) * step_s / 3600
            longest = find_gaps(period, present)
            say(
                source,
                "warning",
                f"{len(runs)} gap(s), {hours:.1f} h with no sample; "
                f"the {len(longest)} longest: {longest}",
            )

    def records() -> None:
        src = sources.usage_records
        assert src is not None
        rows = list(
            src.aggregate(
                AggregateQuery("work", start, end, step_s, ("user", "work"), ("count", "seconds"))
            )
        )
        users = {r.keys.get("user", "") for r in rows}
        say("usage_records", "info", f"{len(rows)} work rows, {len(users)} people")
        if not rows:
            say("usage_records", "warning", "no work rows: are the records reaching ES?")
        for r in rows:
            if r.t_ms is None or not _aligned(r.t_ms, step_ms):
                say("usage_records", "error", f"bucket {r.t_ms} not aligned to {step_s}s")
                break
        for r in rows:
            if any(v < 0 for v in r.values.values()):
                say("usage_records", "error", f"negative sum in {dict(r.keys)}: {dict(r.values)}")
                break
        odd = sorted(
            {
                r.keys.get("work", "")
                for r in rows
                if not r.keys.get("work", "").startswith(WORK_KINDS)
            }
        )
        if odd:
            say("usage_records", "warning", f"unknown kinds of work: {odd[:5]}")
        sandbox = list(
            src.aggregate(AggregateQuery("sandbox", start, end, step_s, ("user",), ("cpu_s",)))
        )
        say("usage_records", "info", f"{len(sandbox)} sandbox rows")
        if not sandbox:
            say(
                "usage_records",
                "warning",
                "no sandbox rows: is sandbox-host sampling (SANDBOX_HOST_USAGE_SAMPLE_S)?",
            )
        else:
            windows = list(
                src.aggregate(
                    AggregateQuery("sandbox", start, end, None, ("window_s",), ("cpu_s",))
                )
            )
            for slot in (step_s, long_step(step_s)):
                for row in windows:
                    window = int(float(row.keys.get("window_s") or 0))
                    if window and slot % window and window % slot:
                        say(
                            "usage_records",
                            "error",
                            f"sandbox rows of a {window}s window cannot be placed in {slot}s "
                            "slots: set SANDBOX_HOST_USAGE_WINDOW_S to a divisor of "
                            f"{step_s}",
                        )
                        break
            pressure = src.aggregate(
                AggregateQuery("sandbox", start, end, None, (), maxes=("cpu_psi_s",))
            )
            if not any("cpu_psi_s" in r.values for r in pressure):
                say(
                    "usage_records",
                    "warning",
                    "sandbox rows carry no CPU pressure: this kernel has no PSI, and view 8 "
                    "(d) cannot show sandboxes waiting for the machine",
                )
        for kind, fields in (
            ("turn", ("accepted_ms", "started_ms", "ended_ms")),
            ("job", ("enqueued_ms", "started_ms", "ended_ms")),
        ):
            found = list(src.records(kind, start, end))
            say("usage_records", "info", f"{len(found)} {kind} records")
            broken = [f for f in found if any(not isinstance(f.get(k), int) for k in fields)]
            if broken:
                say("usage_records", "error", f"{len(broken)} {kind} records miss {fields}")

    def metrics() -> None:
        src = sources.resource_metrics
        assert src is not None
        samples = list(src.usage(start, end, step_s))
        capacity = {c.component: c for c in src.capacity()}
        say("resource_metrics", "info", f"{len(samples)} samples, capacity for {sorted(capacity)}")
        seen = set()
        unaligned("resource_metrics", [s.t_ms for s in samples])
        gaps("resource_metrics", [s.t_ms for s in samples])
        for s in samples:
            if s.component not in COMPONENTS:
                say(
                    "resource_metrics",
                    "error",
                    f"component {s.component!r} is not one of {COMPONENTS}",
                )
                break
        for s in samples:
            seen.add(s.component)
            whole = capacity.get(s.component)
            if (
                whole is not None
                and whole.cpu_cores is not None
                and s.cpu_cores is not None
                and s.cpu_cores > whole.cpu_cores
            ):
                say(
                    "resource_metrics",
                    "error",
                    f"{s.pod} uses {s.cpu_cores:g} cores, more CPU than {s.component} has "
                    f"({whole.cpu_cores:g})",
                )
                break
        for s in samples:
            if any(v is not None and v < 0 for v in (s.cpu_cores, s.memory_bytes, s.disk_bytes)):
                say("resource_metrics", "error", f"negative value from {s.pod}")
                break
        # `worker:<job>` is capacity for the workers too, one job type's
        covered = {name.partition(":")[0] for name in capacity}
        for component in sorted(seen - covered):
            say("resource_metrics", "warning", f"no capacity for {component}: its limit is unknown")
        for c in capacity.values():
            base, colon, job = c.component.partition(":")
            if base not in COMPONENTS or (colon and (base != "worker" or not job)):
                say(
                    "resource_metrics",
                    "error",
                    f"capacity entry {c.component!r} names no component: one of {COMPONENTS}, "
                    "or worker:<job type>",
                )
            if c.machines < 1:
                say("resource_metrics", "error", f"{c.component}: machines must be at least 1")

    def gpus() -> None:
        src = sources.gpu_metrics
        assert src is not None
        samples = list(src.usage(start, end, step_s))
        cards = list(src.cards())
        say("gpu_metrics", "info", f"{len(samples)} samples, {len(cards)} cards")
        unaligned("gpu_metrics", [s.t_ms for s in samples])
        gaps("gpu_metrics", [s.t_ms for s in samples])
        for s in samples:
            if not 0.0 <= s.util <= 1.0:
                say("gpu_metrics", "error", f"util {s.util} on {s.card} is not between 0 and 1")
                break
        models = {c.model for c in cards}
        stray = sorted({s.model for s in samples} - models)
        if stray:
            say("gpu_metrics", "warning", f"samples for models no card serves: {stray[:5]}")

    def llm() -> None:
        src = sources.llm_requests
        assert src is not None
        rows = list(src.usage(start, end, step_s))
        unaligned("llm_requests", [r.t_ms for r in rows])
        ours = [r for r in rows if "ws" in parse_tags(r.tags)]
        garbled = [
            r
            for r in ours
            if any(
                t.startswith("ws.") and (":" not in t or t[3:].partition(":")[0] not in TAG_KEYS)
                for t in r.tags
            )
        ]
        if garbled:
            bad = sorted({t for r in garbled for t in r.tags if t.startswith("ws.")})[:5]
            say(
                "llm_requests",
                "warning",
                f"{len(garbled)} rows carry malformed ws.* tags (e.g. {bad})",
            )
        busy = sum(r.busy_seconds for r in rows)
        share = sum(r.busy_seconds for r in ours) / busy if busy else 0.0
        say(
            "llm_requests",
            "info",
            f"{len(rows)} rows; this platform's share of busy time {share:.0%}",
        )
        if rows and not ours:
            say(
                "llm_requests", "warning", "no row carries this platform's ws tags: is llm_tags on?"
            )
        nameless = [r for r in ours if "user" not in parse_tags(r.tags)]
        if nameless:
            say(
                "llm_requests", "warning", f"{len(nameless)} of this platform's rows name no person"
            )
        latency = list(src.latency(start, end, step_s))
        say("llm_requests", "info", f"{len(latency)} latency rows")
        unaligned("llm_requests", [r.t_ms for r in latency])

    def models() -> None:
        """A turn's model as the platform asked for it against the names
        LiteLLM logs: view 8's per-model first-output pair (b) needs them to
        meet (`same_model`)."""
        turns, logs = sources.usage_records, sources.llm_requests
        assert turns is not None and logs is not None
        asked = {str(t.get("model") or "") for t in turns.records("turn", start, end)} - {""}
        logged = {r.model for r in logs.usage(start, end, step_s)}
        lost = sorted(m for m in asked if not any(same_model(m, name) for name in logged))
        if lost:
            say(
                "llm_requests",
                "warning",
                f"turns name models LiteLLM never logged: {lost[:5]} — view 8's per-model "
                "first-output pair needs LiteLLM to log the name the platform asks for "
                "(its model_group)",
            )

    for name, run in (
        ("usage_records", records),
        ("resource_metrics", metrics),
        ("gpu_metrics", gpus),
        ("llm_requests", llm),
    ):
        if getattr(sources, name) is None:
            say(name, "info", "not connected")
        else:
            _check(name, out, run)
    if sources.usage_records is not None and sources.llm_requests is not None:
        _check("llm_requests", out, models)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    from ..config.loader import load
    from ..factories import get_capacity_sources

    parser = argparse.ArgumentParser(prog="python -m workspace_app.capacity")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="check the configured sources against their contract")
    check.add_argument("--hours", type=float, default=24.0)
    check.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    settings = load(config_path=args.config, env=__import__("os").environ).capacity
    findings = check_sources(
        get_capacity_sources(settings),
        now_ms=int(time.time() * 1000),
        hours=args.hours,
        step_s=settings.step_s,
    )
    marks = {"error": "✗", "warning": "!", "info": "·"}
    for f in findings:
        print(f"{marks[f.level]} {f.source}: {f.message}")
    errors = sum(1 for f in findings if f.level == "error")
    print(f"{errors} error(s)")
    return 1 if errors else 0
