"""Each component × resource, and each person's share of it
(docs/plan-capacity-usage.md §9.1, §9.3).

A `Line` is one thing capacity is bought for — the API's CPU, the sandbox
host's memory, a model's GPUs — measured per slot, split into:

- **per person** (and per App, per purpose): what their work drove;
- **fixed**: what runs with nobody using it (idle pods, the host itself);
- **other**: other systems on a shared GPU, which our N does not drive;
- **residual**: measured minus all of the above — what the regression did not
  explain, kept as its own row so every table adds up to the measured total.

Shared components (API, workers, Postgres) are split by the utilization law:
a pod's CPU-seconds in a slot = its idle cost + Σ cost(kind) × count(kind),
solved for non-negative costs over every slot (NNLS); a person's share is
Σ cost × their own counts. Memory is the same against concurrency (streams,
turns, jobs in flight). Sandboxes, disk and GPUs are measured per person
directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np

from .nnls import nnls
from .observe import Observations, WorkSeries

#: Kept as a column of its own when a route template has at least this many
#: requests in the period and appears in at least `MIN_PRESENCE` of its slots;
#: the rest merge into one column per kind of method.
MIN_REQUESTS = 200
MIN_PRESENCE = 0.05

UNKNOWN = "unknown"


#: Two columns this correlated move together: the regression can only cost
#: them as a pair (Spinner et al. name multicollinearity as the main weakness of
#: regression-based demand estimation). Their per-person sum stays right.
COLLINEAR = 0.95


@dataclass
class FitStats:
    """How well the regression explains its total (view 9)."""

    r2: float | None
    holdout_mape: float | None
    rows: int
    columns: list[tuple[str, float]]
    #: Column pairs that move together; each one's own cost is not separable.
    collinear: list[tuple[str, str]] = field(default_factory=list)


def _collinear(x: np.ndarray, columns: list[str]) -> list[tuple[str, str]]:
    out = []
    moving = [i for i in range(len(columns)) if np.std(x[:, i]) > 0]
    if len(moving) < 2:
        return out
    corr = np.corrcoef(x[:, moving], rowvar=False)
    for a in range(len(moving)):
        for b in range(a + 1, len(moving)):
            if abs(corr[a, b]) >= COLLINEAR:
                out.append((columns[moving[a]], columns[moving[b]]))
    return out


@dataclass
class Line:
    id: str
    component: str
    resource: str
    unit: str
    method: str  # "regression" | "direct" | "fixed"
    total: np.ndarray
    fixed: np.ndarray
    users: list[str] = field(default_factory=list)
    per_user: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))
    by_app: dict[str, np.ndarray] = field(default_factory=dict)
    by_purpose: dict[str, np.ndarray] = field(default_factory=dict)
    other: np.ndarray | None = None
    capacity: float | None = None
    machines: int | None = None
    machine_label: str = ""
    fit: FitStats | None = None
    model: str = ""
    #: The source whose missing data reads as zero in `total` (④ for a model's
    #: in-flight requests, ③ for its memory): a day it has nothing is no day.
    #: Empty when a missing sample is NaN, which marks itself.
    source: str = ""

    @property
    def variable(self) -> np.ndarray:
        if self.per_user.size == 0:
            return np.zeros_like(self.fixed)
        return self.per_user.sum(axis=0, dtype=np.float64)

    @property
    def residual(self) -> np.ndarray:
        other = self.other if self.other is not None else 0.0
        return np.where(
            np.isfinite(self.total), self.total - self.fixed - self.variable - other, 0.0
        )


class _Split:
    """Accumulates one line's per-person, per-App and per-purpose series."""

    def __init__(self, users: list[str], n: int) -> None:
        self.users = users
        self._row = {u: i for i, u in enumerate(users)}
        self.per_user = np.zeros((len(users), n), dtype=np.float32)
        self.by_app: dict[str, np.ndarray] = {}
        self.by_purpose: dict[str, np.ndarray] = {}

    def add(self, user: str, app: str, purpose: str, values: np.ndarray) -> None:
        self.per_user[self._row[user]] += values.astype(np.float32)
        app = app or "(none)"
        self.by_app[app] = self.by_app.get(app, 0.0) + values
        self.by_purpose[purpose] = self.by_purpose.get(purpose, 0.0) + values


def everyone(obs: Observations) -> list[str]:
    """Every person any record names, in a stable order."""
    names: set[str] = set()
    names.update(k[0] for k in obs.work_user)
    names.update(k[0] for k in obs.sandbox_user)
    names.update(k[3] for k in obs.disk)
    names.update(s.user for s in obs.llm if s.ours)
    return sorted(n or UNKNOWN for n in names)


# ── the columns of the shared-component regressions ───────────────────────
def _kept_templates(obs: Observations) -> set[str]:
    n = obs.period.n
    totals: dict[str, float] = {}
    seen: dict[str, np.ndarray] = {}
    for (_pod, _comp, work), series in obs.work_pod.items():
        if work.startswith("req:"):
            totals[work] = totals.get(work, 0.0) + series.total_count()
            slots = seen.get(work)
            if slots is None:
                slots = seen[work] = np.zeros(n, dtype=bool)
            slots[series.slots()] = True
    return {
        work
        for work, total in totals.items()
        if total >= MIN_REQUESTS and seen[work].sum() >= MIN_PRESENCE * n
    }


def cpu_column(work: str, kept: set[str]) -> tuple[str, str, str] | None:
    """(column, which number feeds it, purpose) for a kind of work's CPU."""
    if work.startswith("req:"):
        if work in kept:
            return work, "count", "requests"
        method = work[4:].split(" ", 1)[0]
        other = "req:(other GET)" if method in ("GET", "HEAD") else "req:(other write)"
        return other, "count", "requests"
    if work.startswith("stream:"):
        return "stream", "seconds", "streams"
    if work.startswith("turn-events:"):
        return work, "count", work.split(":", 1)[1]
    if work.startswith("job:"):
        return work, "seconds", work.split(":", 1)[1]
    return None


def memory_column(work: str, _kept: set[str]) -> tuple[str, str, str] | None:
    """Memory follows what is held open: streams, running turns, running jobs."""
    if work.startswith("stream:"):
        return "stream", "seconds", "streams"
    if work.startswith("turn:"):
        return work, "seconds", work.split(":", 1)[1]
    if work.startswith("job:"):
        return work, "seconds", work.split(":", 1)[1]
    return None


Column = Callable[[str, set[str]], tuple[str, str, str] | None]


def _fit(
    ys: dict[str, np.ndarray],
    xs: dict[str, dict[str, np.ndarray]],
    columns: list[str],
    day_of: np.ndarray,
) -> tuple[dict[str, float], dict[str, float], FitStats]:
    """NNLS over every (pod, slot) with a measurement; one intercept per pod.
    Returns (cost per column, idle per pod, fit statistics)."""
    pods = sorted(ys)
    rows_x, rows_y, rows_day = [], [], []
    for p_i, pod in enumerate(pods):
        y = ys[pod]
        ok = np.isfinite(y)
        if not ok.any():
            continue
        cols = [xs.get(pod, {}).get(c, np.zeros_like(y))[ok] for c in columns]
        onehot = np.zeros((int(ok.sum()), len(pods)))
        onehot[:, p_i] = 1.0
        rows_x.append(np.column_stack([*cols, onehot]) if cols else onehot)
        rows_y.append(y[ok])
        rows_day.append(day_of[ok])
    if not rows_y:
        return {}, {}, FitStats(None, None, 0, [])
    x = np.vstack(rows_x)
    y = np.concatenate(rows_y)
    days = np.concatenate(rows_day)
    coef = nnls(x, y)
    fitted = x @ coef
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(((y - fitted) ** 2).sum()) / ss_tot if ss_tot > 0 else None
    mape = _holdout_mape(x, y, days)
    costs = dict(zip(columns, (float(c) for c in coef[: len(columns)]), strict=True))
    idle = {pod: float(coef[len(columns) + i]) for i, pod in enumerate(pods)}
    stats = FitStats(
        r2=r2,
        holdout_mape=mape,
        rows=len(y),
        columns=sorted(costs.items()),
        collinear=_collinear(x[:, : len(columns)], columns),
    )
    return costs, idle, stats


def _holdout_mape(x: np.ndarray, y: np.ndarray, days: np.ndarray) -> float | None:
    """Fit on the first 80% of days, predict each later day's total: the mean
    absolute percentage error of those days (view 9)."""
    distinct = np.unique(days)
    if len(distinct) < 5:
        return None
    cut = distinct[int(len(distinct) * 0.8)]
    train, test = days < cut, days >= cut
    coef = nnls(x[train], y[train])
    errors = []
    for d in np.unique(days[test]):
        here = days == d
        actual = float(y[here].sum())
        if actual > 0:
            errors.append(abs(float((x[here] @ coef).sum()) - actual) / actual)
    return float(np.mean(errors)) if errors else None


def _regression_line(
    obs: Observations,
    users: list[str],
    *,
    line_id: str,
    component: str,
    resource: str,
    unit: str,
    pods: Iterable[str],
    column: Column,
    measure: str,
    work_components: tuple[str, ...],
    pooled: bool = False,
) -> Line | None:
    """A shared component split by the utilization law. `measure` is the pod
    figure regressed ("cpu" in cores, "memory" in bytes); CPU columns enter as
    counts or seconds, memory columns as concurrency (seconds ÷ slot). A
    `pooled` line (Postgres) regresses its pods' sum on every process's work."""
    p = obs.period
    step = float(p.step_s)
    kept = _kept_templates(obs)
    pods = sorted(pods)
    if not pods:
        return None
    cpu = measure == "cpu"

    def feed(series: WorkSeries, which: str) -> np.ndarray:
        values = series.count if which == "count" else series.seconds
        return values if cpu else values / step

    xs: dict[str, dict[str, np.ndarray]] = {}
    ys: dict[str, np.ndarray] = {}
    for comp_pod in pods:
        series = obs.pods[(component, comp_pod)][measure]
        y = series * step if cpu else series.copy()
        if cpu and (local := obs.sandbox_pod.get((comp_pod, component))) is not None:
            y = y - local["cpu_s"]  # sandboxes run inside this pod (`kind: local`)
        elif not cpu and (local := obs.sandbox_pod.get((comp_pod, component))) is not None:
            y = y - local["mem_bs"] / step
        ys[comp_pod] = y
    for (pod, comp, work), series in obs.work_pod.items():
        if comp not in work_components:
            continue
        col = column(work, kept)
        if col is None:
            continue
        name, which, _purpose = col
        target = "*" if pooled else pod
        if not pooled and pod not in ys:
            continue
        acc = xs.setdefault(target, {})
        acc[name] = acc.get(name, 0.0) + feed(series, which)
    if pooled:
        total_y = np.nansum(np.vstack(list(ys.values())), axis=0)
        has = np.any(np.isfinite(np.vstack(list(ys.values()))), axis=0)
        ys = {"*": np.where(has, total_y, np.nan)}
    columns = sorted({c for per_pod in xs.values() for c in per_pod})
    costs, idle, stats = _fit(ys, xs, columns, p.day_of)
    if not idle:
        return None
    split = _Split(users, p.n)
    for (user, app, comp, work), series in obs.work_user.items():
        if comp not in work_components:
            continue
        col = column(work, kept)
        if col is None or col[0] not in costs:
            continue
        name, which, purpose = col
        values = costs[name] * feed(series, which)
        split.add(user, app, purpose, values / step if cpu else values)
    measured = np.vstack([ys[k] for k in ys])
    have = np.isfinite(measured)
    total = np.where(have.any(axis=0), np.nansum(measured, axis=0), np.nan)
    fixed = sum(np.where(have[i], idle[k], 0.0) for i, k in enumerate(ys))
    if cpu:
        total, fixed = total / step, np.asarray(fixed) / step
    return Line(
        id=line_id,
        component=component,
        resource=resource,
        unit=unit,
        method="regression",
        total=total,
        fixed=np.asarray(fixed, dtype=float),
        users=users,
        per_user=split.per_user,
        by_app=split.by_app,
        by_purpose=split.by_purpose,
        fit=stats,
    )


def _pods_of(obs: Observations, component: str, measure: str) -> list[str]:
    return [
        pod
        for (comp, pod), m in obs.pods.items()
        if comp == component and np.isfinite(m[measure]).any()
    ]


def _summed(obs: Observations, component: str, measure: str) -> np.ndarray:
    arrays = [m[measure] for (comp, _pod), m in obs.pods.items() if comp == component]
    if not arrays:
        return np.full(obs.period.n, np.nan)
    stack = np.vstack(arrays)
    return np.where(np.isfinite(stack).any(axis=0), np.nansum(stack, axis=0), np.nan)


def _direct_line(
    obs: Observations,
    users: list[str],
    split: _Split,
    *,
    line_id: str,
    component: str,
    resource: str,
    unit: str,
    measured: np.ndarray,
) -> Line:
    """Measured per person; the component's total, when ② reports it, minus
    their sum is the component's own overhead (fixed)."""
    variable = split.per_user.sum(axis=0, dtype=np.float64)
    total = np.where(np.isfinite(measured), measured, variable)
    fixed = np.clip(total - variable, 0.0, None)
    return Line(
        id=line_id,
        component=component,
        resource=resource,
        unit=unit,
        method="direct",
        total=total,
        fixed=fixed,
        users=users,
        per_user=split.per_user,
        by_app=split.by_app,
        by_purpose=split.by_purpose,
    )


def _fixed_line(line_id: str, component: str, resource: str, unit: str, total: np.ndarray) -> Line:
    return Line(
        id=line_id,
        component=component,
        resource=resource,
        unit=unit,
        method="fixed",
        total=total,
        fixed=np.where(np.isfinite(total), total, 0.0),
    )


def build_lines(obs: Observations) -> list[Line]:
    """Every line the observations support. A line whose source is missing is
    simply not there; the readiness of each view says why."""
    p = obs.period
    step = float(p.step_s)
    users = everyone(obs)
    lines: list[Line] = []
    if obs.connected("usage_records") and obs.connected("resource_metrics"):
        for resource, measure, unit, column in (
            ("cpu", "cpu", "cores", cpu_column),
            ("memory", "memory", "bytes", memory_column),
        ):
            for component, work_components in (("api", ("api",)), ("worker", ("worker",))):
                line = _regression_line(
                    obs,
                    users,
                    line_id=f"{component}.{resource}",
                    component=component,
                    resource=resource,
                    unit=unit,
                    pods=_pods_of(obs, component, measure),
                    column=column,
                    measure=measure,
                    work_components=work_components,
                )
                if line is not None:
                    lines.append(line)
        pg = _regression_line(
            obs,
            users,
            line_id="postgres.cpu",
            component="postgres",
            resource="cpu",
            unit="cores",
            pods=_pods_of(obs, "postgres", "cpu"),
            column=cpu_column,
            measure="cpu",
            work_components=("api", "worker"),
            pooled=True,
        )
        if pg is not None:
            lines.append(pg)
    if obs.connected("resource_metrics"):
        for resource, unit in (("memory", "bytes"), ("disk", "bytes")):
            total = _summed(obs, "postgres", resource)
            if np.isfinite(total).any():
                lines.append(_fixed_line(f"postgres.{resource}", "postgres", resource, unit, total))
    if obs.connected("usage_records") and obs.sandbox_user:
        for resource, field_, unit in (("cpu", "cpu_s", "cores"), ("memory", "mem_bs", "bytes")):
            split = _Split(users, p.n)
            for (user, app), m in obs.sandbox_user.items():
                split.add(user, app, "sandbox", m[field_] / step)
            lines.append(
                _direct_line(
                    obs,
                    users,
                    split,
                    line_id=f"sandbox-host.{resource}",
                    component="sandbox-host",
                    resource=resource,
                    unit=unit,
                    measured=_summed(obs, "sandbox-host", resource),
                )
            )
    if obs.connected("usage_records") and obs.disk:
        split = _Split(users, p.n)
        per_day: dict[tuple[str, str, str], np.ndarray] = {}
        for (day, scope, _ident, user, app), size in obs.disk.items():
            key = (user, app, scope)
            per_day.setdefault(key, np.zeros(len(p.days)))[day] += size
        for (user, app, scope), daily in per_day.items():
            split.add(user, app, scope, daily[p.day_of])
        lines.append(
            _direct_line(
                obs,
                users,
                split,
                line_id="filestore.disk",
                component="filestore",
                resource="disk",
                unit="bytes",
                measured=_summed(obs, "filestore", "disk"),
            )
        )
    if obs.connected("llm_requests"):
        for model in sorted({s.model for s in obs.llm}):
            split = _Split(users, p.n)
            other = np.zeros(p.n)
            for s in obs.llm:
                if s.model != model:
                    continue
                if s.ours:
                    split.add(s.user, s.app, s.purpose or "request", s.busy / step)
                else:
                    other += s.busy / step
            variable = split.per_user.sum(axis=0, dtype=np.float64)
            lines.append(
                Line(
                    id=f"gpu.{model}",
                    component="gpu",
                    resource="gpu",
                    unit="inflight",
                    method="direct",
                    total=variable + other,
                    fixed=np.zeros(p.n),
                    users=users,
                    per_user=split.per_user,
                    by_app=split.by_app,
                    by_purpose=split.by_purpose,
                    other=other,
                    model=model,
                    source="llm_requests",
                )
            )
    if obs.connected("gpu_metrics"):
        for model, m in sorted(obs.gpu.items()):
            line = _fixed_line(f"gpu.{model}.memory", "gpu", "memory", "bytes", m["memory"])
            line.model = model
            line.source = "gpu_metrics"
            line.capacity = obs.gpu_memory_total.get(model)
            lines.append(line)
    _attach_capacity(obs, lines)
    return lines


def _attach_capacity(obs: Observations, lines: list[Line]) -> None:
    """② says what each component may use, and what one more machine is."""
    for entry in obs.capacity:
        for line in lines:
            if line.component != entry.component:
                continue
            value = {
                "cpu": entry.cpu_cores,
                "memory": entry.memory_bytes,
                "disk": entry.disk_bytes,
            }.get(line.resource)
            if value is not None:
                line.capacity = float(value)
                line.machines = entry.machines
                line.machine_label = entry.machine_label
