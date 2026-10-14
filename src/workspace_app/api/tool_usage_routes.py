"""The administrator's tool-usage report (docs/plan-tool-usage-stats.md §3.6).

Superusers only (D2). A window of 7, 30 or 90 days ending today (D5); per tool,
calls / failures / average time (D3), filterable by source (D6), surface and
App (D7); and one tool's breakdown by person (D4), by surface × App and by item.
`kind=skill` asks the same of skill loads (§8.3); without it, tools.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from specstar import SpecStar

from ..agent.tool_usage import SEP
from .locator import ItemLocator
from .tool_usage_store import breakdown, per_tool, rows_since, since_day

#: The windows a report may ask for (D5).
DAYS = (7, 30, 90)


def _window(days: int) -> int:
    # Checked by hand, not typed as a `Literal[7, 30, 90]`: FastAPI does not
    # coerce a query string to an int literal, so every value would be refused.
    if days not in DAYS:
        raise HTTPException(status_code=422, detail="days must be 7, 30 or 90")
    return days


#: What a report counts (§8.3).
KINDS = ("tool", "skill")


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="kind must be tool or skill")
    return kind


def _avg(calls: int, ms: int) -> int:
    return round(ms / calls) if calls else 0


class ToolUsageTool(BaseModel):
    tool: str
    source: str
    calls: int
    fails: int
    avg_ms: int


class ToolUsageReport(BaseModel):
    days: int
    tools: list[ToolUsageTool]


class ByUser(BaseModel):
    user: str
    calls: int
    fails: int
    avg_ms: int


class BySurfaceApp(BaseModel):
    surface: str
    app: str
    calls: int
    fails: int
    avg_ms: int


class ByItem(BaseModel):
    item: str
    #: The item's title and App, when it still exists — "" when it does not.
    title: str
    slug: str
    calls: int
    fails: int
    avg_ms: int


class ToolUsageDetail(BaseModel):
    tool: str
    days: int
    by_user: list[ByUser]
    by_surface_app: list[BySurfaceApp]
    by_item: list[ByItem]


def register_tool_usage_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
    superusers: frozenset[str],
) -> None:
    def _require_superuser() -> None:
        if get_user_id() not in superusers:
            raise HTTPException(status_code=403, detail="superusers only")

    @app.get("/admin/tool-usage", response_model=ToolUsageReport)
    async def tool_usage(
        days: int = 30, source: str = "", surface: str = "", app: str = "", kind: str = "tool"
    ) -> ToolUsageReport:
        _require_superuser()
        days = _window(days)
        kind = _kind(kind)
        rows = await asyncio.to_thread(rows_since, spec, since_day(days), kind=kind)
        tools = [
            ToolUsageTool(tool=t, source=s, calls=c, fails=f, avg_ms=_avg(c, m))
            for t, s, c, f, m in per_tool(rows, source=source, surface=surface, app=app)
        ]
        tools.sort(key=lambda t: (-t.calls, t.tool))
        return ToolUsageReport(days=days, tools=tools)

    @app.get("/admin/tool-usage/{tool}", response_model=ToolUsageDetail)
    async def tool_usage_detail(tool: str, days: int = 30, kind: str = "tool") -> ToolUsageDetail:
        _require_superuser()
        days = _window(days)
        kind = _kind(kind)
        rows = await asyncio.to_thread(rows_since, spec, since_day(days), tool=tool, kind=kind)

        by_user = sorted(
            (
                ByUser(user=u, calls=c, fails=f, avg_ms=_avg(c, m))
                for u, (c, f, m) in breakdown(rows, "by_user").items()
            ),
            key=lambda x: (-x.calls, x.user),
        )
        by_surface_app = sorted(
            (
                BySurfaceApp(
                    surface=k.split(SEP, 1)[0],
                    app=k.split(SEP, 1)[-1] if SEP in k else "",
                    calls=c,
                    fails=f,
                    avg_ms=_avg(c, m),
                )
                for k, (c, f, m) in breakdown(rows, "by_surface_app").items()
            ),
            key=lambda x: (-x.calls, x.surface, x.app),
        )

        def item_row(item: str, c: int, f: int, m: int) -> ByItem:
            return ByItem(
                item=item,
                title=(locator.title_of(item) or "") if item else "",
                slug=(locator.slug_of(item) or "") if item else "",
                calls=c,
                fails=f,
                avg_ms=_avg(c, m),
            )

        items = breakdown(rows, "by_item")
        by_item = await asyncio.to_thread(
            lambda: [item_row(i, c, f, m) for i, (c, f, m) in items.items()]
        )
        by_item.sort(key=lambda x: (-x.calls, x.item))
        return ToolUsageDetail(
            tool=tool,
            days=days,
            by_user=by_user,
            by_surface_app=by_surface_app,
            by_item=by_item,
        )
