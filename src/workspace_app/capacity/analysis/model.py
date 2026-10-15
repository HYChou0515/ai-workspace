"""One analysis of one period, and the views drawn from it
(docs/plan-capacity-usage.md §9, §10).

`analyse` turns observations into lines, people, thresholds, a simulator for
what-if and headroom, and a backtest; `Analysis.summary()` is what the page
loads, `person()` and `whatif()` what it asks for on demand. Nothing is
written anywhere (D3): the service keeps one `Analysis` per period in memory.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .lines import Line, build_lines, everyone
from .observe import Observations
from .people import NOT_PEOPLE, People, count_people
from .period import Period
from .runway import fit_trend, reach
from .thresholds import Pair, build_pairs, pair_json
from .whatif import DRAWS, MAX_GROWTH, Headroom, Simulator, headroom

#: Until a knee is seen (E9, my numbers — the page labels them as defaults).
DEFAULT_THRESHOLD = {"cpu": 0.7, "memory": 0.8, "disk": 0.8}
#: "Peak" for a person: their mean demand over the line's busiest 5% of slots (E8).
PEAK_SHARE = 0.05
#: "Heavy" = the heaviest tenth of people (view 6's profile).
HEAVY_SHARE = 0.10
#: E11: rolling one-day-ahead over the last 14 days; above 20% is 「僅供參考」.
BACKTEST_DAYS = 14
BACKTEST_DRAWS = 100
MAPE_OK = 0.20
#: §9.10, how much is needed before each view says anything.
NEED = {
    "overview": 1,
    "allocation": 1,
    "regression": 3,
    "distribution": 7,
    "trends": 2,
    "whatif": 14,
    "runway": 28,
    "thresholds": 7,
    "backtest": 21,
}
MIN_PEOPLE = 10
#: What-if profiles whose simulator is kept: each copies every person's day
#: demand, so one per App ever asked about would add up without end.
SIMULATORS_KEPT = 4
SOURCES = ("usage_records", "resource_metrics", "gpu_metrics", "llm_requests")
#: The sources the what-if rests on: it sizes only on days all those connected have.
MODEL_SOURCES = ("usage_records", "resource_metrics", "llm_requests")


def _default_workday(day: dt.date) -> bool:
    return day.weekday() < 5


def data_days(obs: Observations, sources: Sequence[str]) -> list[int]:
    """The period's whole days on which every one of `sources` has data."""
    p = obs.period
    ok = np.ones(len(p.days), dtype=bool)
    for source in sources:
        ok &= obs.days_with(source)
    return [d for d in p.full_days if ok[d]]


def model_days(obs: Observations) -> list[int]:
    """The days the what-if sizes on and the backtest tests: whole days on
    which every connected source of the model has data. A day before the
    records began would read as a day nobody worked."""
    return data_days(obs, [s for s in MODEL_SOURCES if obs.connected(s)])


@dataclass
class Analysis:
    obs: Observations
    lines: list[Line]
    people: People
    pairs: list[Pair]
    thresholds: dict[str, tuple[float, str]]
    #: `model_days`, and the workdays among them (all of them when none is one).
    model_days: list[int]
    sizing_days: list[int]
    headroom: Headroom | None
    backtest: dict[str, Any] | None
    generated_ms: int
    is_workday: Callable[[dt.date], bool] = _default_workday
    #: most recently used last; asked from the routes' threads, hence the lock
    _sims: OrderedDict[str, Simulator] = field(default_factory=OrderedDict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _summary: dict[str, Any] | None = field(default=None, repr=False)
    _readiness: dict[str, dict[str, Any]] | None = field(default=None, repr=False)

    @property
    def by_id(self) -> dict[str, Line]:
        return {line.id: line for line in self.lines}

    # ── what each view needs ──────────────────────────────────────────────
    def runway_days(self) -> list[int]:
        """Recorded days whose N is whole: the people trend is fitted on them
        only, since a count still filling up rises with no one new."""
        complete = self.people.complete
        return [
            d
            for d in data_days(self.obs, ["usage_records"])
            if complete is None or bool(complete[d])
        ]

    def readiness(self) -> dict[str, dict[str, Any]]:
        """Per view: whether it can say anything yet, and if not, why — a
        source not connected or failing, a period shorter than the view needs
        (no wait will fix that), days still accumulating, too few people.
        The same for as long as the analysis is kept: worked out once."""
        if self._readiness is None:
            self._readiness = self._build_readiness()
        return self._readiness

    def _build_readiness(self) -> dict[str, dict[str, Any]]:
        holds = len(self.obs.period.full_days)
        n_now = int(self.people.active_30d[-1]) if len(self.people.active_30d) else 0
        regressions = any(line.method == "regression" for line in self.lines)

        def view(  # noqa: ANN202
            need: int,
            sources: Sequence[str],
            *,
            have: int | None = None,
            any_of: bool = False,
            people: bool = False,
            extra: str | None = None,
        ):
            status = self.obs.status
            failing = [s for s in sources if s in status and status[s].error is not None]
            absent = [s for s in sources if not self.obs.connected(s) and s not in failing]
            usable = [s for s in sources if self.obs.connected(s)]
            if any_of and usable:
                failing, absent = [], []
            if have is None:
                if any_of:
                    have = sum(
                        1
                        for d in self.obs.period.full_days
                        if any(self.obs.days_with(s)[d] for s in usable)
                    )
                else:
                    have = len(data_days(self.obs, usable))
            reason = None
            if failing:
                reason = "source_error"
            elif absent:
                reason = "source_missing"
            elif holds < need:
                reason = "period_too_short"
            elif have < need:
                reason = "accumulating"
            elif people and n_now < MIN_PEOPLE:
                reason = "too_few_people"
            elif extra:
                reason = extra
            return {
                "ready": reason is None,
                "reason": reason,
                "missing": failing or absent,
                "error": status[failing[0]].error if failing else None,
                "have_days": have,
                "need_days": need,
                "period_days": holds,
            }

        model = len(self.model_days)
        both = ("usage_records", "resource_metrics")
        return {
            "overview": view(NEED["overview"], ["resource_metrics"]),
            "allocation": view(NEED["allocation"], ["usage_records"]),
            "person": view(NEED["allocation"], ["usage_records"]),
            # Lines split by regression, within views 2 and 3: what each kind
            # of work costs needs days of both sources (§9.10).
            "regression": view(NEED["regression"], both),
            "distribution": view(NEED["distribution"], ["usage_records"], people=True),
            "trends": view(NEED["trends"], SOURCES, any_of=True),
            "whatif": view(
                NEED["whatif"],
                both,
                have=model,
                people=True,
                extra=None if regressions else "no_regression",
            ),
            "runway": view(NEED["runway"], both, have=len(self.runway_days()), people=True),
            "thresholds": view(NEED["thresholds"], both),
            "confidence": view(NEED["backtest"], both, have=model),
        }

    # ── the page ──────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        """What the page loads — the same for as long as the analysis is kept,
        so it is worked out once."""
        if self._summary is None:
            self._summary = self._build_summary()
        return self._summary

    def _build_summary(self) -> dict[str, Any]:
        p = self.obs.period
        return {
            "period": {
                "start_ms": p.start_ms,
                "end_ms": p.end_ms,
                "step_s": p.step_s,
                "timezone": str(p.tz),
                "days": [d.isoformat() for d in p.days],
                "full_days": p.full_days,
                "workdays": [d for d in range(len(p.days)) if self.is_workday(p.days[d])],
                "generated_ms": self.generated_ms,
            },
            "sources": {
                k: {"connected": s.connected, "error": s.error, "rows": s.rows}
                for k, s in self.obs.status.items()
            },
            "readiness": self.readiness(),
            "people": {
                "n_now": int(self.people.active_30d[-1]) if len(self.people.active_30d) else 0,
                "active_30d": self.people.active_30d.tolist(),
                "active_today": self.people.active_today.tolist(),
                "complete": self.people.complete is None or bool(self.people.complete[-1]),
                "recorded_since_ms": self.obs.recorded_since_ms,
            },
            "lines": [self._line_json(line) for line in self.lines],
            "headroom": None if self.headroom is None else self._headroom_json(),
            "runway": self._runway_json(),
            "thresholds": [pair_json(pair) for pair in self.pairs],
            "confidence": self._confidence_json(),
        }

    def _busy(self, line: Line) -> np.ndarray:
        total = np.where(np.isfinite(line.total), line.total, -np.inf)
        if not np.isfinite(total).any():
            return np.zeros(0, dtype=np.int64)
        cut = np.quantile(total[np.isfinite(total)], 1 - PEAK_SHARE)
        return np.flatnonzero(total >= cut)

    def _daily(self, series: np.ndarray, how: str) -> list[float | None]:
        p = self.obs.period
        out: list[float | None] = []
        for d in range(len(p.days)):
            values = series[p.day_of == d]
            values = values[np.isfinite(values)]
            if not len(values):
                out.append(None)
            else:
                out.append(float(values.max() if how == "max" else values.mean()))
        return out

    def _line_json(self, line: Line) -> dict[str, Any]:
        p = self.obs.period
        busy = self._busy(line)

        def stat(series: np.ndarray | None) -> dict[str, float] | None:
            if series is None:
                return None
            finite = np.where(np.isfinite(series), series, 0.0)
            return {
                "avg": float(finite.mean()) if len(finite) else 0.0,
                "peak": float(finite[busy].mean()) if len(busy) else 0.0,
            }

        groups: dict[str, list[dict[str, Any]]] = {"person": [], "app": [], "purpose": []}
        if line.per_user.size:
            avg = line.per_user.mean(axis=1, dtype=np.float64)
            peak = (
                line.per_user[:, busy].mean(axis=1, dtype=np.float64)
                if len(busy)
                else np.zeros(len(line.rows))
            )
            groups["person"] = [
                {"key": line.users[r], "avg": float(a), "peak": float(k)}
                for r, a, k in zip(line.rows, avg, peak, strict=True)
                if a > 0 or k > 0
            ]
            for name, by in (("app", line.by_app), ("purpose", line.by_purpose)):
                groups[name] = [
                    {"key": key, **(stat(series) or {})} for key, series in sorted(by.items())
                ]
        person_peaks = sorted(g["peak"] for g in groups["person"] if g["key"] not in NOT_PEOPLE)
        daily_peak = self._daily(line.total, "max")
        own = data_days(self.obs, [line.source]) if line.source else p.full_days
        # the days the headroom sizes on, so a row's peak, utilisation and
        # "holds" rest on the same days; before it can size, the line's own
        days = [d for d in self.sizing_days if d in own] or own
        full_peaks = [v for d in days if (v := daily_peak[d]) is not None]
        threshold, source = self.thresholds.get(line.id, (None, None))
        capacity = line.capacity
        peak = float(np.quantile(full_peaks, 0.95)) if full_peaks else None
        total = stat(line.total) if np.isfinite(line.total).any() else None
        return {
            "id": line.id,
            "component": line.component,
            "resource": line.resource,
            "unit": line.unit,
            "model": line.model,
            "job": line.job,
            "method": line.method,
            "capacity": capacity,
            "machines": line.machines,
            "machine_label": line.machine_label,
            "threshold": threshold,
            "threshold_source": source,
            "peak": peak,
            "avg": total["avg"] if total else None,
            "utilization_peak": (peak / capacity) if (peak is not None and capacity) else None,
            "fixed": stat(line.fixed),
            "other": stat(line.other),
            "residual": stat(line.residual),
            "total": total,
            "groups": groups,
            "daily": {"peak": daily_peak, "avg": self._daily(line.total, "mean")},
            "fit": None
            if line.fit is None
            else {
                "r2": line.fit.r2,
                "holdout_mape": line.fit.holdout_mape,
                "holdout_days": line.fit.holdout_days,
                "rows": line.fit.rows,
                "columns": [[c, v] for c, v in line.fit.columns],
                "collinear": [list(pair) for pair in line.fit.collinear],
            },
            "distribution": _distribution(person_peaks),
        }

    def _headroom_json(self) -> dict[str, Any]:
        room = self.headroom
        assert room is not None
        return {
            "n_now": room.n_now,
            "max_n": room.max_n,
            "binding": room.binding,
            "by_line": room.by_line,
            "ceiling": room.max_n is not None and room.max_n >= max(1, room.n_now) * MAX_GROWTH,
            # what it rests on: the days it drew from
            "days": len(self.sizing_days),
        }

    def _runway_json(self) -> dict[str, Any]:
        p = self.obs.period
        days = self.runway_days()
        x = np.array(days, dtype=float)
        n = self.people.active_30d[days].astype(float) if days else np.zeros(0)
        trend = fit_trend(x, n)
        room = self.headroom
        reached = None
        if trend is not None and room is not None and room.max_n is not None:
            # from today, the period's last day — the day the page dates from
            reached = {"target": room.max_n, **reach(trend, float(len(p.days) - 1), room.max_n)}
        band: list[list[float]] = []
        if trend is not None:
            fit, half = trend.intercept + trend.slope * x, trend.half(x)
            band = np.column_stack([fit - half, fit + half]).tolist()
        out: dict[str, Any] = {
            "days": days,
            "n": n.tolist(),
            "trend": trend.json() if trend is not None else None,
            "band": band,
            "reach": reached,
            "disk": [],
        }
        n_now = int(self.people.active_30d[-1]) if len(self.people.active_30d) else 0
        for line in self.lines:
            if line.resource != "disk" or not line.capacity:
                continue
            daily = self._daily(line.total, "max")
            xs = np.array([d for d in p.full_days if daily[d] is not None], dtype=float)
            ys = np.array([daily[d] for d in p.full_days if daily[d] is not None], dtype=float)
            threshold = self.thresholds.get(line.id, (DEFAULT_THRESHOLD["disk"], "default"))[0]
            grows = fit_trend(xs, ys)
            out["disk"].append(
                {
                    "line": line.id,
                    "trend": grows.json() if grows is not None else None,
                    "limit": line.capacity * threshold,
                    "now": float(ys[-1]) if len(ys) else None,
                    # §9.1 (E16): what one more person costs in disk, a month
                    "per_person_month": grows.slope * 30 / n_now if grows and n_now else None,
                }
            )
        return out

    def _confidence_json(self) -> dict[str, Any]:
        p = self.obs.period
        # A source's coverage and its gaps are one question: the slots with
        # anything from it. ② and ③ are sampled every slot, so a run without
        # is a gap; ① and ④ have rows only when something happened (on k8s,
        # ① has the probes every slot), so they have coverage but no gaps.
        recorded = np.zeros(p.n, dtype=bool)
        for series in self.obs.work_pod.values():
            recorded[series.slots()] = True
        sampled = np.zeros(p.n, dtype=bool)
        for m in self.obs.pods.values():
            sampled[m.slots("cpu")] = True
            sampled[m.slots("memory")] = True
        carded = np.zeros(p.n, dtype=bool)
        for m in self.obs.gpu.values():
            carded |= (m["util"] > 0) | (m["memory"] > 0)
        asked = np.zeros(p.n, dtype=bool)
        for s in self.obs.llm:
            asked[s.slots()] = True
        records = self.obs.connected("usage_records")
        coverage: dict[str, float | None] = {
            "usage_records": float(recorded.mean()) if records else None,
            "resource_metrics": float(sampled.mean()) if self.obs.pods else None,
            "gpu_metrics": float(carded.mean()) if self.obs.gpu else None,
            "llm_requests": float(asked.mean()) if self.obs.llm else None,
        }
        gaps = {"resource_metrics": find_gaps(p, sampled), "gpu_metrics": find_gaps(p, carded)}
        # Unidentified: of the API requests someone made — a static file is
        # nobody's to identify, and a probe is system work. System: of the
        # running time of jobs and turns, the work that can have nobody
        # behind it.
        requests = unknown = ran = system = 0.0
        for (user, _app, _comp, work), series in self.obs.work_user.items():
            if work.startswith("req:") and not work.endswith(" (static)") and user != "system":
                total = series.total_count()
                requests += total
                if user == "unknown":
                    unknown += total
            elif work.startswith(("job:", "turn:")):
                seconds = series.total_seconds()
                ran += seconds
                if user == "system":
                    system += seconds
        untagged: dict[str, float] = {}
        for line in self.lines:
            if line.unit == "inflight" and line.other is not None:
                total = float(np.nansum(line.total))
                untagged[line.model] = float(line.other.sum()) / total if total else 0.0
        return {
            "regressions": [
                {
                    "line": line.id,
                    "r2": line.fit.r2,
                    "holdout_mape": line.fit.holdout_mape,
                    "holdout_days": line.fit.holdout_days,
                    "columns": len(line.fit.columns),
                    "collinear": [list(c) for c in line.fit.collinear],
                }
                for line in self.lines
                if line.fit is not None
            ],
            "backtest": self.backtest,
            "coverage": coverage,
            "unknown_share": unknown / requests if requests else None,
            "system_share": system / ran if ran else None,
            "gaps": gaps,
            "untagged_gpu_share": untagged,
            "dropped": self.obs.dropped,
            "mape_ok": MAPE_OK,
        }

    # ── on demand ────────────────────────────────────────────────────────
    def _app_people(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for user, app, _comp, _work in self.obs.work_user:
            if app:
                out.setdefault(app, set()).add(user)
        for user, app in self.obs.sandbox_user:
            if app:
                out.setdefault(app, set()).add(user)
        return out

    def _heavy(self) -> set[int]:
        users = self.people.users
        score = np.zeros(len(users))
        for line in self.lines:
            if not line.per_user.size or line.resource == "disk":
                continue
            totals = line.person_totals()
            whole = totals.sum()
            if whole > 0:
                score += totals / whole
        candidates = [i for i, u in enumerate(users) if u not in NOT_PEOPLE and score[i] > 0]
        candidates.sort(key=lambda i: -score[i])
        return set(candidates[: max(1, math.ceil(len(candidates) * HEAVY_SHARE))])

    def simulator(self, profile: str = "all", app: str = "") -> Simulator | None:
        key = f"{profile}:{app}"
        with self._lock:
            if key in self._sims:
                self._sims.move_to_end(key)
                return self._sims[key]
        if not self.sizing_days:
            return None
        if profile == "heavy":
            heavy = np.array(sorted(self._heavy()), dtype=np.int64)
            pool = lambda present: np.flatnonzero(np.isin(present, heavy))  # noqa: E731
        elif profile == "app":
            names = self._app_people().get(app, set())
            rows = np.array(
                [i for i, u in enumerate(self.people.users) if u in names], dtype=np.int64
            )
            pool = lambda present: np.flatnonzero(np.isin(present, rows))  # noqa: E731
        else:
            pool = None
        sim = Simulator(self.lines, self.people, self.obs.period, days=self.sizing_days, pool=pool)
        with self._lock:
            self._sims[key] = sim
            self._sims.move_to_end(key)
            while len(self._sims) > SIMULATORS_KEPT:
                self._sims.popitem(last=False)
        return sim

    def limits(self) -> dict[str, float]:
        """Each line N can push past: capacity × safe threshold. A fixed line
        (Postgres memory, a model's preallocated GPU memory) and disk (a stock,
        not a peak — view 7 runs it out over time) do not grow with N, so they
        cannot bind it; the overview still shows how full they are."""
        out = {}
        for line in self.lines:
            threshold = self.thresholds.get(line.id, (None, None))[0]
            if line.capacity and threshold and line.method != "fixed" and line.resource != "disk":
                out[line.id] = line.capacity * threshold
        return out

    def whatif(self, n: int, profile: str = "all", app: str = "") -> dict[str, Any]:
        # Asked directly, it does not answer what the page says it cannot.
        sim = self.simulator(profile, app) if self.readiness()["whatif"]["ready"] else None
        if sim is None:
            return {
                "n": n,
                "n_asked": n,
                "n_max": None,
                "profile": profile,
                "app": app,
                "lines": [],
                "ready": False,
            }
        asked = n
        # As far as the headroom looks (20× today); the answer says which N it is.
        n_max = max(1, sim.n_now) * MAX_GROWTH
        n = max(0, min(n, n_max))
        peaks = sim.run(n)
        rows = []
        fixed_ids = {line.id for line in sim.fixed_lines}
        for line in self.lines:
            if line.id not in peaks:
                continue
            p50, p95 = (float(np.quantile(peaks[line.id], q)) for q in (0.5, 0.95))
            threshold = self.thresholds.get(line.id, (None, None))[0]
            per_machine = line.capacity / line.machines if line.capacity and line.machines else None
            # A line N cannot push (a model's preallocated memory, the database's
            # memory) needs what it uses at any N: no threshold, no verdict.
            fixed = line.id in fixed_ids
            needed = {
                q: (v if fixed else v / threshold if threshold else None)
                for q, v in (("p50", p50), ("p95", p95))
            }
            rows.append(
                {
                    "id": line.id,
                    "unit": line.unit,
                    "fixed": fixed,
                    "p50": p50,
                    "p95": p95,
                    "capacity": line.capacity,
                    "threshold": threshold,
                    "needed_p50": needed["p50"],
                    "needed_p95": needed["p95"],
                    "machines_now": line.machines,
                    "machine_label": line.machine_label,
                    "machines_needed": {
                        q: (
                            math.ceil(v / per_machine)
                            if (not fixed and v is not None and per_machine)
                            else None
                        )
                        for q, v in needed.items()
                    },
                    "fits": {
                        q: (
                            v <= line.capacity
                            if (not fixed and v is not None and line.capacity)
                            else None
                        )
                        for q, v in needed.items()
                    },
                }
            )
        return {
            "n": n,
            "n_asked": asked,
            "n_max": n_max,
            "n_now": sim.n_now,
            "profile": profile,
            "app": app,
            "draws": DRAWS,
            "lines": rows,
            "ready": True,
        }

    def person(self, user: str) -> dict[str, Any] | None:
        if user not in self.people.users:
            return None
        p = self.obs.period
        row = self.people.users.index(user)
        lines = []
        people = np.array([u not in NOT_PEOPLE for u in self.people.users])
        for line in self.lines:
            if not line.users:
                continue
            mine = line.of(row)
            totals = line.person_totals()
            # ranked among people: the unknown and system rows are not people,
            # and have no rank among them
            ranked = totals[people]
            rank = (
                None
                if user in NOT_PEOPLE
                else float((ranked < totals[row]).sum() / max(1, len(ranked) - 1))
            )
            lines.append(
                {
                    "id": line.id,
                    "unit": line.unit,
                    "avg": float(mine.mean()),
                    "daily_avg": self._daily(mine, "mean"),
                    "daily_peak": self._daily(mine, "max"),
                    "share": float(totals[row] / totals.sum()) if totals.sum() else 0.0,
                    "rank": rank,
                }
            )
        apps: dict[str, int] = {}
        for u, app, _comp, _work in self.obs.work_user:
            if u == user and app:
                apps[app] = apps.get(app, 0) + 1
        items: dict[str, dict[str, float]] = {}
        for (u, item), cpu_s in self.obs.sandbox_items.items():
            if u == user:
                items.setdefault(item, {})["sandbox_cpu_s"] = cpu_s
        for (_day, scope, ident, u, _app), size in self.obs.disk.items():
            if u == user:
                entry = items.setdefault(ident, {})
                entry["disk_bytes"] = max(entry.get("disk_bytes", 0.0), size)
                entry["scope"] = scope  # ty: ignore[invalid-assignment]
        turns = [t for t in self.obs.turns if t.get("user") == user]
        for t in turns:
            entry = items.setdefault(str(t.get("item", "")), {})
            entry["turns"] = entry.get("turns", 0) + 1
        slots = [s.slots() for (u, _a, _c, _w), s in self.obs.work_user.items() if u == user]
        active = len(np.unique(p.day_of[np.concatenate(slots)])) if slots else 0
        return {
            "user": user,
            "lines": lines,
            "apps": sorted(apps),
            "items": [{"id": k, **v} for k, v in sorted(items.items())],
            "turns": len(turns),
            "jobs": sum(1 for j in self.obs.jobs if j.get("user") == user),
            "active_days": active,
        }


#: A run of this many slots with nothing is a gap (15 minutes at 5-minute slots).
GAP_SLOTS = 3
#: The longest gaps listed per source.
MAX_GAPS = 5


def gap_runs(present: np.ndarray) -> list[tuple[int, int]]:
    """Every run of at least `GAP_SLOTS` slots with nothing, as [start, end)
    slot indices in time order — from the source's first slot with
    something to the period's end: a source that stopped before the end left
    a gap to it, though nothing follows."""
    have = np.flatnonzero(present)
    if not len(have):
        return []
    runs: list[tuple[int, int]] = []
    start = None
    for i in range(int(have[0]), len(present) + 1):
        if i < len(present) and not present[i]:
            start = i if start is None else start
        elif start is not None:
            if i - start >= GAP_SLOTS:
                runs.append((start, i))
            start = None
    return runs


def find_gaps(p: Period, present: np.ndarray) -> list[list[int]]:
    """The `MAX_GAPS` longest gaps (`gap_runs`), as [start_ms, end_ms] in
    time order (view 9)."""
    longest = sorted(sorted(gap_runs(present), key=lambda r: r[0] - r[1])[:MAX_GAPS])
    return [[p.start_ms + a * p.step_ms, p.start_ms + b * p.step_ms] for a, b in longest]


def _distribution(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    arr = np.array(values)
    top = max(1, math.ceil(len(arr) * 0.1))
    whole = arr.sum()
    return {
        "p50": float(np.quantile(arr, 0.5)),
        "p90": float(np.quantile(arr, 0.9)),
        "p99": float(np.quantile(arr, 0.99)),
        "top10_share": float(np.sort(arr)[-top:].sum() / whole) if whole else None,
        "values": [float(v) for v in arr],
    }


def _thresholds(
    lines: list[Line], pairs: list[Pair], cards: dict[str, int]
) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for line in lines:
        knees = [pair.knee.at for pair in pairs if pair.sets == line.id and pair.knee is not None]
        if line.unit == "inflight":
            if knees:
                # A model's capacity is its in-flight level at the knee (E5).
                line.capacity = min(knees)
                line.machines = cards.get(line.model) or None
                out[line.id] = (1.0, "knee")
            continue
        if knees:
            out[line.id] = (min(knees), "knee")
        elif line.resource in DEFAULT_THRESHOLD:
            out[line.id] = (DEFAULT_THRESHOLD[line.resource], "default")
    return out


def _backtest(
    lines: list[Line], people: People, obs: Observations, is_workday, days: list[int]
) -> dict[str, Any] | None:  # noqa: ANN001
    """Each of the last 14 of `days` predicted from the days before it."""
    p = obs.period
    full = days
    if len(full) < NEED["backtest"]:
        return None
    errors: dict[str, list[float]] = {}
    tested = []
    for t in full[-BACKTEST_DAYS:]:
        kind = is_workday(p.days[t])
        prior = [d for d in full if d < t and is_workday(p.days[d]) == kind][-28:]
        if len(prior) < 5:
            continue
        sim = Simulator(lines, people, p, days=prior, draws=BACKTEST_DRAWS, seed=t)
        peaks = sim.run(int(people.active_30d[t]))
        slots = p.day_of == t
        for line in sim.lines:
            actual = line.total[slots]
            actual = actual[np.isfinite(actual)]
            if not len(actual) or actual.max() <= 0:
                continue
            predicted = float(np.quantile(peaks[line.id], 0.5))
            errors.setdefault(line.id, []).append(abs(predicted - actual.max()) / actual.max())
        tested.append(t)
    if not errors:
        return None
    by_line = {k: float(np.mean(v)) for k, v in errors.items()}
    return {"mape": float(np.mean(list(by_line.values()))), "days": tested, "by_line": by_line}


def analyse(
    obs: Observations,
    *,
    now_ms: int,
    is_workday: Callable[[dt.date], bool] = _default_workday,
) -> Analysis:
    lines = build_lines(obs)
    users = lines[0].users if lines and lines[0].users else everyone(obs)
    for line in lines:
        if line.users and line.users != users:
            raise AssertionError("every line shares one list of people")
    people = count_people(obs, users)
    by_id = {line.id: line for line in lines}
    pairs = build_pairs(obs, by_id)
    thresholds = _thresholds(lines, pairs, obs.cards)
    p = obs.period
    days = model_days(obs)
    workdays = [d for d in days if is_workday(p.days[d])]
    sizing = workdays or days
    analysis = Analysis(
        obs=obs,
        lines=lines,
        people=people,
        pairs=pairs,
        thresholds=thresholds,
        model_days=days,
        sizing_days=sizing,
        headroom=None,
        backtest=None,
        generated_ms=now_ms,
        is_workday=is_workday,
    )
    sim = analysis.simulator() if len(days) >= NEED["whatif"] else None
    if sim is not None:
        analysis.headroom = headroom(sim, analysis.limits())
    analysis.backtest = _backtest(lines, people, obs, is_workday, days)
    return analysis
