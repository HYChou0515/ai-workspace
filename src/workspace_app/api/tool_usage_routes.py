"""The administrator's tool-usage report (docs/plan-tool-usage-stats.md §3.6).

Superusers only (D2). A window of 7, 30 or 90 days ending today (D5); per tool,
calls / failures / average time (D3), filterable by source (D6), surface and
App (D7); and one tool's breakdown by person (D4), by surface × App and by item.
`kind=skill` asks the same of skill loads (§8.3); without it, tools.
`group=package` adds a package's commands into one row (§9.1), and
`/admin/tool-usage.csv` hands over the raw cells (§9.3).
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
from collections.abc import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from specstar import SpecStar

from ..agent.tool_usage import SEP
from .locator import ItemLocator
from .tool_usage_store import (
    RAW_COLUMNS,
    ToolUsageDay,
    breakdown,
    per_tool,
    raw_lines,
    rows_since,
    since_day,
)

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


#: How a report groups tools (§9.1).
GROUPS = ("command", "package")


def _group(group: str) -> str:
    if group not in GROUPS:
        raise HTTPException(status_code=422, detail="group must be command or package")
    return group


def _rows_of(spec: SpecStar, since: str, name: str, kind: str, group: str) -> list[ToolUsageDay]:
    """The rows one report line adds up: a tool's — or, by package, every
    command of the package `name`, and a built-in of that name (it has none)."""
    if group != "package":
        return rows_since(spec, since, tool=name, kind=kind)
    of_package = rows_since(spec, since, package=name, kind=kind)
    alone = [r for r in rows_since(spec, since, tool=name, kind=kind) if not r.package]
    return of_package + alone


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

    @app.get("/admin/tool-usage.csv")
    async def tool_usage_raw(days: int = 30, kind: str = "tool") -> Response:
        _require_superuser()
        days = _window(days)
        kind = _kind(kind)
        rows = await asyncio.to_thread(rows_since, spec, since_day(days), kind=kind)
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=RAW_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(raw_lines(rows))
        name = f"tool-usage-{kind}-{days}d-{dt.datetime.now(dt.UTC).date().isoformat()}.csv"
        # UTF-8 with a BOM: Excel opens it as UTF-8 only when it sees one.
        return Response(
            content=out.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/admin/tool-usage", response_model=ToolUsageReport)
    async def tool_usage(
        days: int = 30,
        source: str = "",
        surface: str = "",
        app: str = "",
        kind: str = "tool",
        group: str = "command",
    ) -> ToolUsageReport:
        _require_superuser()
        days = _window(days)
        kind = _kind(kind)
        group = _group(group)
        rows = await asyncio.to_thread(rows_since, spec, since_day(days), kind=kind)
        tools = [
            ToolUsageTool(tool=t, source=s, calls=c, fails=f, avg_ms=_avg(c, m))
            for t, s, c, f, m in per_tool(
                rows, source=source, surface=surface, app=app, group=group
            )
        ]
        tools.sort(key=lambda t: (-t.calls, t.tool))
        return ToolUsageReport(days=days, tools=tools)

    @app.get("/admin/tool-usage/{tool}", response_model=ToolUsageDetail)
    async def tool_usage_detail(
        tool: str, days: int = 30, kind: str = "tool", group: str = "command", source: str = ""
    ) -> ToolUsageDetail:
        _require_superuser()
        days = _window(days)
        kind = _kind(kind)
        group = _group(group)
        rows = await asyncio.to_thread(_rows_of, spec, since_day(days), tool, kind, group)
        # One name can come from two sources (a hub copy of a skill written
        # elsewhere): the row asked about is one of them.
        if source:
            rows = [r for r in rows if r.source == source]

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
