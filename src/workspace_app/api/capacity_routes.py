"""The capacity dashboard's API (docs/plan-capacity-usage.md §10, §11).

Superusers only (D4), with the tool-usage gate's shape: 403 for anyone else.
Every response is one analysis of one period (7, 28 or 90 days), built on
demand by `CapacityService` and kept in memory; nothing is stored (D3).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel

from ..capacity.service import PERIODS, CapacityService, TooManyRows

# ── the summary (views 1, 2, 4, 5, 8, 9) ─────────────────────────────────────


class PeriodOut(BaseModel):
    start_ms: int
    end_ms: int
    step_s: int
    timezone: str
    days: list[str]
    full_days: list[int]
    workdays: list[int]
    generated_ms: int


class SourceOut(BaseModel):
    connected: bool
    error: str | None
    rows: int


class ReadinessOut(BaseModel):
    ready: bool
    #: source_error | source_missing | period_too_short | accumulating |
    #: too_few_people | no_regression
    reason: str | None
    #: the sources failing (source_error) or not connected (source_missing)
    missing: list[str]
    #: what the first failing source raised
    error: str | None
    have_days: int
    need_days: int
    #: the whole days this period holds; fewer than need_days → period_too_short
    period_days: int


class PeopleOut(BaseModel):
    n_now: int
    active_30d: list[int]
    active_today: list[int]
    #: whether today's N rests on 30 recorded days (False: it counts only the
    #: people seen since the records began, and reads low)
    complete: bool
    recorded_since_ms: int | None


class StatOut(BaseModel):
    avg: float
    peak: float


class GroupOut(BaseModel):
    key: str
    avg: float
    peak: float


class GroupsOut(BaseModel):
    person: list[GroupOut]
    app: list[GroupOut]
    purpose: list[GroupOut]


class DailyOut(BaseModel):
    peak: list[float | None]
    avg: list[float | None]


class FitOut(BaseModel):
    r2: float | None
    holdout_mape: float | None
    #: how many of the last days that error predicted
    holdout_days: int
    rows: int
    columns: list[tuple[str, float]]
    collinear: list[list[str]]


class DistributionOut(BaseModel):
    p50: float
    p90: float
    p99: float
    top10_share: float | None
    values: list[float]


class LineOut(BaseModel):
    id: str
    component: str
    resource: str
    #: cores | bytes | inflight
    unit: str
    model: str
    #: a worker line's job type ("other": pods that ran none); empty otherwise
    job: str
    #: regression | direct | fixed
    method: str
    capacity: float | None
    machines: int | None
    machine_label: str
    threshold: float | None
    #: knee | default
    threshold_source: str | None
    peak: float | None
    avg: float | None
    utilization_peak: float | None
    fixed: StatOut | None
    other: StatOut | None
    residual: StatOut | None
    total: StatOut | None
    groups: GroupsOut
    daily: DailyOut
    fit: FitOut | None
    distribution: DistributionOut | None


class HeadroomOut(BaseModel):
    n_now: int
    max_n: int | None
    binding: str | None
    by_line: dict[str, int]
    ceiling: bool


class TrendOut(BaseModel):
    intercept: float
    slope: float
    residual_sd: float


class DiskRunwayOut(BaseModel):
    line: str
    trend: TrendOut | None
    limit: float
    now: float | None
    #: the trend's growth a month ÷ today's N: one more person's disk a month
    per_person_month: float | None


class RunwayOut(BaseModel):
    days: list[int]
    n: list[float]
    trend: TrendOut | None
    disk: list[DiskRunwayOut]


class KneeOut(BaseModel):
    at: float
    slope_before: float
    slope_after: float
    gain: float


class PairOut(BaseModel):
    id: str
    model: str
    x: str
    y: str
    sets: str | None
    points: list[list[float]]
    #: (d) only: the share of sandbox time held at the sandbox's own limit
    throttled: list[list[float]] | None
    #: why there is nothing to show, when that is not "it never slowed" (no_psi)
    note: str | None
    #: (e) only: the job type whose queue it is ("" when the workers are one line)
    job: str
    x_max: float | None
    span: float
    knee: KneeOut | None


class RegressionOut(BaseModel):
    line: str
    r2: float | None
    holdout_mape: float | None
    #: how many of the last days that error predicted
    holdout_days: int
    columns: int
    collinear: list[list[str]]


class BacktestOut(BaseModel):
    mape: float
    days: list[int]
    by_line: dict[str, float]


class ConfidenceOut(BaseModel):
    regressions: list[RegressionOut]
    backtest: BacktestOut | None
    coverage: dict[str, float | None]
    unknown_share: float | None
    #: E4: the share of requests no person asked for
    system_share: float | None
    #: source → [start_ms, end_ms] runs with nothing, for the sources sampled
    #: every slot (resource_metrics, gpu_metrics)
    gaps: dict[str, list[list[int]]]
    untagged_gpu_share: dict[str, float]
    dropped: float
    mape_ok: float


class CapacitySummary(BaseModel):
    period: PeriodOut
    sources: dict[str, SourceOut]
    readiness: dict[str, ReadinessOut]
    people: PeopleOut
    lines: list[LineOut]
    headroom: HeadroomOut | None
    runway: RunwayOut
    thresholds: list[PairOut]
    confidence: ConfidenceOut
    #: whether anything measures sandboxes one by one (§6.5): False — nothing
    #: does on this backend; None — sandbox-host decides, not this process
    sandbox_measured: bool | None


# ── one person (view 3) ──────────────────────────────────────────────────────


class PersonLineOut(BaseModel):
    id: str
    unit: str
    avg: float
    daily_avg: list[float | None]
    daily_peak: list[float | None]
    share: float
    rank: float


class PersonItemOut(BaseModel):
    id: str
    sandbox_cpu_s: float | None = None
    disk_bytes: float | None = None
    scope: str | None = None
    turns: int | None = None


class PersonOut(BaseModel):
    user: str
    lines: list[PersonLineOut]
    apps: list[str]
    items: list[PersonItemOut]
    turns: int
    jobs: int
    active_days: int


# ── what N people would need (view 6) ────────────────────────────────────────


class WhatIfLineOut(BaseModel):
    id: str
    unit: str
    p50: float
    p95: float
    capacity: float | None
    threshold: float | None
    needed_p50: float | None
    needed_p95: float | None
    machines_now: int | None
    machine_label: str
    machines_needed: dict[str, int | None]
    fits: dict[str, bool | None]


class WhatIfOut(BaseModel):
    #: the N answered — the one asked, up to n_max
    n: int
    n_asked: int
    #: the furthest the what-if looks: 20× today's N (None: not ready)
    n_max: int | None
    n_now: int | None = None
    profile: str
    app: str
    draws: int | None = None
    lines: list[WhatIfLineOut]
    ready: bool


def register_capacity_routes(
    app: FastAPI | APIRouter,
    *,
    service: CapacityService,
    get_user_id: Callable[[], str],
    superusers: frozenset[str],
    sandbox_measured: bool | None = None,
) -> None:
    def _require_superuser() -> None:
        if get_user_id() not in superusers:
            raise HTTPException(status_code=403, detail="superusers only")

    def _period(days: int) -> int:
        if days not in PERIODS:
            raise HTTPException(status_code=422, detail=f"days must be one of {list(PERIODS)}")
        return days

    async def _analysis(days: int):  # noqa: ANN202
        try:
            return await service.analysis(_period(days))
        except TooManyRows as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "too_many_rows", "rows": exc.rows, "max_rows": exc.limit},
            ) from exc

    @app.get("/admin/capacity", response_model=CapacitySummary)
    async def capacity_summary(days: int = 28) -> CapacitySummary:
        _require_superuser()
        analysis = await _analysis(days)
        # Each answer walks the whole analysis: off the loop, like the build.
        summary = await asyncio.to_thread(analysis.summary)
        return CapacitySummary.model_validate({**summary, "sandbox_measured": sandbox_measured})

    @app.get("/admin/capacity/person", response_model=PersonOut)
    async def capacity_person(user: str, days: int = 28) -> PersonOut:
        _require_superuser()
        analysis = await _analysis(days)
        found = await asyncio.to_thread(analysis.person, user)
        if found is None:
            raise HTTPException(status_code=404, detail="no use recorded for this person")
        return PersonOut.model_validate(found)

    @app.get("/admin/capacity/whatif", response_model=WhatIfOut)
    async def capacity_whatif(
        n: int, days: int = 28, profile: Literal["all", "heavy", "app"] = "all", app: str = ""
    ) -> WhatIfOut:
        _require_superuser()
        analysis = await _analysis(days)
        return WhatIfOut.model_validate(await asyncio.to_thread(analysis.whatif, n, profile, app))

    @app.post("/admin/capacity/refresh", status_code=204)
    async def capacity_refresh(days: int = 28) -> Response:
        _require_superuser()
        service.drop(_period(days))
        return Response(status_code=204)
