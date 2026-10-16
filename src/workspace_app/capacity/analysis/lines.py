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

from .nnls import nnls_normal
from .observe import Observations, WorkSeries
from .period import GAP_SLOTS

#: Kept as a column of its own when a route template has at least this many
#: requests in the period and appears in at least `MIN_PRESENCE` of its slots;
#: the rest merge into one column per method (§9.3).
MIN_REQUESTS = 200
MIN_PRESENCE = 0.05
#: §9.9: a regression's error predicting the last 7 days from the ones before,
#: which need at least 3 (the regression lines' own readiness, §9.10).
HOLDOUT_DAYS = 7
MIN_TRAIN_DAYS = 3

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
    #: how many of the last days `holdout_mape` predicted (up to `HOLDOUT_DAYS`)
    holdout_days: int = 0


def _collinear(
    g: np.ndarray, sums: np.ndarray, rows: int, columns: list[str]
) -> list[tuple[str, str]]:
    """Column pairs whose correlation over the rows is at least `COLLINEAR`,
    from the columns' cross-products and sums."""
    if rows < 2:
        return []
    mean = sums / rows
    cov = (g - rows * np.outer(mean, mean)) / (rows - 1)
    var = np.diag(cov)
    scale = np.diag(g) / rows
    moving = [i for i in range(len(columns)) if var[i] > 1e-12 * max(scale[i], 1e-300)]
    out = []
    for a, i in enumerate(moving):
        for j in moving[a + 1 :]:
            if abs(cov[i, j]) / np.sqrt(var[i] * var[j]) >= COLLINEAR:
                out.append((columns[i], columns[j]))
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
    #: Everyone any record names — the same list on every line split by
    #: person; empty on a fixed line.
    users: list[str] = field(default_factory=list)
    #: The people this line costs, as positions in `users` (ascending), one
    #: row of `per_user` each. Someone it costs nothing has no row: a job
    #: type three people ran is three rows, not one per person ever seen.
    rows: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    #: [row, slot] what each of `rows` drove (float32)
    per_user: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))
    by_app: dict[str, np.ndarray] = field(default_factory=dict)
    by_purpose: dict[str, np.ndarray] = field(default_factory=dict)
    other: np.ndarray | None = None
    capacity: float | None = None
    #: Machines of `capacity` — a worker line's share of a shared entry's
    #: machines is fractional.
    machines: float | None = None
    machine_label: str = ""
    fit: FitStats | None = None
    model: str = ""
    #: The source whose missing data reads as zero in `total` (④ for a model's
    #: in-flight requests, ③ for its memory): a day it has nothing is no day.
    #: Empty when a missing sample is NaN, which marks itself.
    source: str = ""
    #: A worker line's job type (§9.1: one line per job type); "other" for
    #: pods that ran none in the period; empty for every other line.
    job: str = ""
    #: A worker line's pods now (`_pods_now`) — its share of the `worker`
    #: capacity entry when no `worker:<job>` names it.
    pods_now: int = 0

    def __post_init__(self) -> None:
        if len(self.rows) != self.per_user.shape[0]:
            raise ValueError(f"{self.id}: {len(self.rows)} rows for {self.per_user.shape[0]}")

    @property
    def variable(self) -> np.ndarray:
        if self.per_user.size == 0:
            return np.zeros_like(self.fixed)
        return self.per_user.sum(axis=0, dtype=np.float64)

    def of(self, person: int) -> np.ndarray:
        """[slot] what `users[person]` drove — zeros when the line costs them nothing."""
        at = int(np.searchsorted(self.rows, person))
        if at < len(self.rows) and self.rows[at] == person:
            return self.per_user[at].astype(np.float64)
        return np.zeros(len(self.total))

    def person_totals(self) -> np.ndarray:
        """[person] what each of `users` drove over the period."""
        out = np.zeros(len(self.users))
        if self.per_user.size:
            out[self.rows] = self.per_user.sum(axis=1, dtype=np.float64)
        return out

    @property
    def residual(self) -> np.ndarray:
        other = self.other if self.other is not None else 0.0
        return np.where(
            np.isfinite(self.total), self.total - self.fixed - self.variable - other, 0.0
        )


class _Split:
    """Accumulates one line's per-person, per-App and per-purpose series — a
    row only for each person something is added for."""

    def __init__(self, users: list[str], n: int) -> None:
        self.users = users
        self._n = n
        self._row = {u: i for i, u in enumerate(users)}
        self._acc: dict[int, np.ndarray] = {}
        self.by_app: dict[str, np.ndarray] = {}
        self.by_purpose: dict[str, np.ndarray] = {}

    def add(self, user: str, app: str, purpose: str, values: np.ndarray) -> None:
        if not values.any():
            return
        person = self._row[user]
        acc = self._acc.get(person)
        if acc is None:
            acc = self._acc[person] = np.zeros(self._n, dtype=np.float32)
        acc += values.astype(np.float32)
        app = app or "(none)"
        self.by_app[app] = self.by_app.get(app, 0.0) + values
        self.by_purpose[purpose] = self.by_purpose.get(purpose, 0.0) + values

    def rows(self) -> tuple[np.ndarray, np.ndarray]:
        """(positions in `users`, [row, slot] demand), in `users` order."""
        order = sorted(self._acc)
        if not order:
            return np.zeros(0, dtype=np.int64), np.zeros((0, self._n), dtype=np.float32)
        per_user = np.vstack([self._acc.pop(person) for person in order])
        return np.array(order, dtype=np.int64), per_user


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
        return f"req:(other {method})", "count", "requests"
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


class _Normal:
    """The cross-products the solver needs (`nnls_normal`), summed one pod's
    block at a time: the regression's columns and one intercept per pod."""

    def __init__(self, columns: int, pods: int) -> None:
        self.c = columns
        k = columns + pods
        self.g = np.zeros((k, k))
        self.b = np.zeros(k)

    def add(self, x: np.ndarray, y: np.ndarray, pod: int) -> None:
        c, j = self.c, self.c + pod
        self.g[:c, :c] += x.T @ x
        sums = x.sum(axis=0)
        self.g[:c, j] += sums
        self.g[j, :c] += sums
        self.g[j, j] += len(y)
        self.b[:c] += x.T @ y
        self.b[j] += y.sum()


def _fit(
    ys: dict[str, tuple[np.ndarray, np.ndarray]],
    block: Callable[[str, np.ndarray], np.ndarray],
    columns: list[str],
    day_of: np.ndarray,
) -> tuple[dict[str, float], dict[str, float], FitStats]:
    """NNLS over every (pod, slot) with a measurement; one intercept per pod.
    `ys[pod]` is (slots, figure) where the pod was measured, `block(pod, at)`
    its columns there. The rows are never laid out together — every rollout
    names new pods, each another intercept column, and rows × columns of a
    90-day period grew past what the API pod should hold — so memory follows
    columns², not rows. Returns (cost per column, idle per pod, statistics).

    §9.9: the error predicting each of the last `HOLDOUT_DAYS` days from the
    ones before (at least `MIN_TRAIN_DAYS` of them). A pod the training days
    never saw is not scored: one born after the cut — every rollout renames
    them — has no idle cost to predict from."""
    pods = [pod for pod in sorted(ys) if len(ys[pod][0])]
    if not pods:
        return {}, {}, FitStats(None, None, 0, [])
    c = len(columns)
    distinct = np.unique(np.concatenate([day_of[ys[pod][0]] for pod in pods]))
    held = max(0, min(HOLDOUT_DAYS, len(distinct) - MIN_TRAIN_DAYS))
    cut = distinct[-held] if held else None
    whole, train = _Normal(c, len(pods)), _Normal(c, len(pods))
    # each held-out day: its scored rows' column sums, and their figure
    day_x = {int(d): np.zeros(c + len(pods)) for d in distinct[len(distinct) - held :]}
    day_y = dict.fromkeys(day_x, 0.0)
    yy = sy = 0.0
    rows = 0
    for i, pod in enumerate(pods):
        at, y = ys[pod]
        x = block(pod, at)
        whole.add(x, y, i)
        yy += float(y @ y)
        sy += float(y.sum())
        rows += len(y)
        if cut is None:
            continue
        days = day_of[at]
        before = days < cut
        if not before.any():
            continue
        train.add(x[before], y[before], i)
        for d in np.unique(days[~before]):
            here = days == d
            day_x[int(d)][:c] += x[here].sum(axis=0)
            day_x[int(d)][c + i] += here.sum()
            day_y[int(d)] += float(y[here].sum())
    coef = nnls_normal(whole.g, whole.b)
    ss_tot = yy - sy * sy / rows
    ss_res = max(0.0, yy - 2.0 * float(coef @ whole.b) + float(coef @ whole.g @ coef))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    mape = None
    if cut is not None:
        held_coef = nnls_normal(train.g, train.b)
        errors = [
            abs(float(day_x[d] @ held_coef) - actual) / actual
            for d, actual in day_y.items()
            if actual > 0
        ]
        mape = float(np.mean(errors)) if errors else None
    costs = dict(zip(columns, (float(v) for v in coef[:c]), strict=True))
    idle = {pod: float(coef[c + i]) for i, pod in enumerate(pods)}
    sums = whole.g[:c, c:].sum(axis=1)
    stats = FitStats(
        r2=r2,
        holdout_mape=mape,
        rows=rows,
        columns=sorted(costs.items()),
        collinear=_collinear(whole.g[:c, :c], sums, rows, columns),
        holdout_days=held,
    )
    return costs, idle, stats


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
    kept: set[str],
    pooled: bool = False,
) -> Line | None:
    """A shared component split by the utilization law. `measure` is the pod
    figure regressed ("cpu" in cores, "memory" in bytes); CPU columns enter as
    counts or seconds, memory columns as concurrency (seconds ÷ slot). A
    `pooled` line (Postgres) regresses its pods' sum on every process's work."""
    p = obs.period
    step = float(p.step_s)
    pods = sorted(pods)
    if not pods:
        return None
    cpu = measure == "cpu"

    def feed(series: WorkSeries, which: str) -> np.ndarray:
        values = series.count if which == "count" else series.seconds
        return values if cpu else values / step

    # Each pod's figure where it was measured: (slots, values).
    ys: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for comp_pod in pods:
        samples = obs.pods[(component, comp_pod)]
        at = samples.slots(measure)
        y = samples[measure][at] * step if cpu else samples[measure][at]
        if (local := obs.sandbox_pod.get((comp_pod, component))) is not None:
            # sandboxes run inside this pod (`kind: local`)
            y = y - (local["cpu_s"][at] if cpu else local["mem_bs"][at] / step)
        ys[comp_pod] = (at, y)
    if pooled:
        total_y, has = np.zeros(p.n), np.zeros(p.n, dtype=bool)
        for at, y in ys.values():
            total_y[at] += y
            has[at] = True
        at = np.flatnonzero(has)
        ys = {"*": (at, total_y[at])}
    # What feeds each column of each pod: the series, not dense copies of them.
    feeds: dict[str, list[tuple[str, WorkSeries, str]]] = {}
    for (pod, comp, work), series in obs.work_pod.items():
        if comp not in work_components:
            continue
        col = column(work, kept)
        if col is None:
            continue
        target = "*" if pooled else pod
        if target in ys:
            feeds.setdefault(target, []).append((col[0], series, col[1]))
    columns = sorted({name for fed in feeds.values() for name, _s, _w in fed})
    where = {name: i for i, name in enumerate(columns)}

    def block(target: str, at: np.ndarray) -> np.ndarray:
        x = np.zeros((len(at), len(columns)))
        for name, series, which in feeds.get(target, ()):
            x[:, where[name]] += feed(series, which)[at]
        return x

    costs, idle, stats = _fit(ys, block, columns, p.day_of)
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
    total, fixed = np.zeros(p.n), np.zeros(p.n)
    have = np.zeros(p.n, dtype=bool)
    for target, (at, y) in ys.items():
        total[at] += y
        fixed[at] += idle.get(target, 0.0)
        have[at] = True
    total[~have] = np.nan
    if cpu:
        total, fixed = total / step, fixed / step
    rows, per_user = split.rows()
    return Line(
        id=line_id,
        component=component,
        resource=resource,
        unit=unit,
        method="regression",
        total=total,
        fixed=fixed,
        users=users,
        rows=rows,
        per_user=per_user,
        by_app=split.by_app,
        by_purpose=split.by_purpose,
        fit=stats,
    )


def _pods_of(obs: Observations, component: str, measure: str) -> list[str]:
    return [
        pod for (comp, pod), m in obs.pods.items() if comp == component and len(m.slots(measure))
    ]


def _worker_groups(obs: Observations, measure: str) -> dict[str, list[str]]:
    """Worker pods by the job type they ran (§9.1: a pod's job type is read
    off its `work` rows); a worker process consumes one. A pod that ran none
    in the period is "other". A pod that ran several leaves no clean split,
    and the workers stay one line (job "")."""
    ran: dict[str, set[str]] = {}
    for pod, comp, work in obs.work_pod:
        if comp == "worker" and work.startswith("job:"):
            ran.setdefault(pod, set()).add(work[4:])
    pods = _pods_of(obs, "worker", measure)
    if any(len(jobs) > 1 for jobs in ran.values()):
        return {"": pods} if pods else {}
    groups: dict[str, list[str]] = {}
    for pod in pods:
        jobs = ran.get(pod)
        groups.setdefault(next(iter(jobs)) if jobs else "other", []).append(pod)
    return groups


def _last_slot(obs: Observations, component: str, measure: str) -> int | None:
    """The last slot any of the component's pods reported."""
    last = [
        int(at[-1])
        for (comp, _p), m in obs.pods.items()
        if comp == component
        if len(at := m.slots(measure))
    ]
    return max(last) if last else None


def _pods_now(obs: Observations, component: str, pods: list[str], measure: str, last: int) -> int:
    """The middle of how many of `pods` were running in each of the
    `GAP_SLOTS` slots up to `last` — a pod runs in a slot it reported in, and
    in one it missed between two it reported in. One slot off — a scrape
    missed, or a rollout's old and new pod side by side — moves nothing.
    Counted in the last slot alone, a
    missed scrape took a job type's whole share; at the most of the slots, a
    surge pod took one pod more. A change that lasts two slots counts."""
    counts = np.zeros(GAP_SLOTS, dtype=np.int64)
    first = last - GAP_SLOTS + 1
    for pod in pods:
        seen = set(obs.pods[(component, pod)].slots(measure).tolist())
        for k, slot in enumerate(range(first, last + 1)):
            # a pod seen on both sides of a slot it missed was running in it:
            # the median forgives one slot off for the type, not one per pod
            if slot in seen or (slot - 1 in seen and slot + 1 in seen):
                counts[k] += 1
    return int(np.median(counts))


def _summed(obs: Observations, component: str, measure: str) -> np.ndarray:
    """[slot] the component's pods summed; NaN where none of them reported."""
    total, have = np.zeros(obs.period.n), np.zeros(obs.period.n, dtype=bool)
    for (comp, _pod), m in obs.pods.items():
        if comp == component and len(at := m.slots(measure)):
            total[at] += m[measure][at]
            have[at] = True
    total[~have] = np.nan
    return total


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
    rows, per_user = split.rows()
    variable = per_user.sum(axis=0, dtype=np.float64) if len(rows) else np.zeros(obs.period.n)
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
        rows=rows,
        per_user=per_user,
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
        kept = _kept_templates(obs)
        for resource, measure, unit, column in (
            ("cpu", "cpu", "cores", cpu_column),
            ("memory", "memory", "bytes", memory_column),
        ):
            groups = [("", "api", _pods_of(obs, "api", measure))]
            groups += [(job, "worker", pods) for job, pods in _worker_groups(obs, measure).items()]
            # One slot for every worker line, so their shares of the pool add up.
            last = _last_slot(obs, "worker", measure)
            for job, component, pods in groups:
                line = _regression_line(
                    obs,
                    users,
                    line_id=f"{component}.{job}.{resource}" if job else f"{component}.{resource}",
                    component=component,
                    resource=resource,
                    unit=unit,
                    pods=pods,
                    column=column,
                    measure=measure,
                    work_components=(component,),
                    kept=kept,
                )
                if line is not None:
                    line.job = job
                    if component == "worker" and last is not None:
                        line.pods_now = _pods_now(obs, component, pods, measure, last)
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
            kept=kept,
            pooled=True,
        )
        if pg is not None:
            lines.append(pg)
    if obs.connected("resource_metrics"):
        for resource, unit in (("memory", "bytes"), ("disk", "bytes")):
            total = _summed(obs, "postgres", resource)
            if np.isfinite(total).any():
                lines.append(_fixed_line(f"postgres.{resource}", "postgres", resource, unit, total))
        # The sandboxes' scratch volume (§9.1): nobody's in particular.
        scratch = _summed(obs, "scratch", "disk")
        if np.isfinite(scratch).any():
            lines.append(_fixed_line("scratch.disk", "scratch", "disk", "bytes", scratch))
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
            rows, per_user = split.rows()
            variable = per_user.sum(axis=0, dtype=np.float64) if len(rows) else np.zeros(p.n)
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
                    rows=rows,
                    per_user=per_user,
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
    """② says what each component may use, and what one more machine is. A
    worker line of one job type takes the `worker:<job>` entry when ② names
    one. Otherwise it takes its share of the `worker` entry: its pods now, of
    all the pods of the worker lines sharing that entry (each Deployment's
    pods taken as alike) — so the shares add up to the entry, and `machines`
    is that share of the entry's machines, fractional. "Now" is the last slot
    any worker pod reported, and the two before it (`_pods_now`)."""
    entries = {entry.component: entry for entry in obs.capacity}
    sharing: dict[str, int] = {}
    for line in lines:
        if line.job and f"{line.component}:{line.job}" not in entries:
            sharing[line.resource] = sharing.get(line.resource, 0) + line.pods_now
    for line in lines:
        own = entries.get(f"{line.component}:{line.job}") if line.job else None
        pooled = entries.get(line.component)
        machines: float | None
        if own is not None:
            entry, share, machines = own, 1.0, own.machines
        elif pooled is None:
            continue
        elif line.job:
            if not line.pods_now:
                continue
            share = line.pods_now / sharing[line.resource]
            entry, machines = pooled, (pooled.machines * share if pooled.machines else None)
        else:
            entry, share, machines = pooled, 1.0, pooled.machines
        value = {
            "cpu": entry.cpu_cores,
            "memory": entry.memory_bytes,
            "disk": entry.disk_bytes,
        }.get(line.resource)
        if value is not None:
            line.capacity = float(value) * share
            line.machines = machines
            line.machine_label = entry.machine_label
