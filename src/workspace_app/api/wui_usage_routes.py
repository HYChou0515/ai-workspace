"""The administrator's report of deployed pages' use (docs/plan-wui-usage.md U8).

Superusers only (U5). A window of 7, 30 or 90 days ending today, or every day
(`days=0`, U12); per page, person-days opened and each kind of use with its
time (U2–U4); one page's people; and the raw cells as CSV — the shape the tool
report has (`tool_usage_routes`), so the two read alike.
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

from .locator import ItemLocator
from .wui_usage import RAW_COLUMNS, per_page, per_person, raw_lines, rows_in_window

#: The windows a report may ask for; 0 is every day (U12).
DAYS = (7, 30, 90, 0)


def _window(days: int) -> int:
    # Checked by hand, as the tool report does: FastAPI does not coerce a query
    # string to an int literal.
    if days not in DAYS:
        raise HTTPException(status_code=422, detail="days must be 7, 30, 90 or 0 (every day)")
    return days


class Kinds(BaseModel):
    """Each kind's uses and milliseconds, and their sum (U3/U4)."""

    workflow: int
    workflow_ms: int
    tool: int
    tool_ms: int
    agent: int
    agent_ms: int
    uses: int
    uses_ms: int


def _kinds(cells: tuple[int, ...]) -> dict[str, int]:
    wf, wf_ms, tool, tool_ms, agent, agent_ms = cells
    return {
        "workflow": wf,
        "workflow_ms": wf_ms,
        "tool": tool,
        "tool_ms": tool_ms,
        "agent": agent,
        "agent_ms": agent_ms,
        "uses": wf + tool + agent,
        "uses_ms": wf_ms + tool_ms + agent_ms,
    }


class WuiUsagePage(Kinds):
    item_id: str
    page: str
    #: The item's title and App, when it still exists — "" when it does not.
    title: str
    slug: str
    person_days: int


class WuiUsageReport(BaseModel):
    days: int
    pages: list[WuiUsagePage]


class WuiUsagePerson(Kinds):
    user: str
    person_days: int
    opens: int


class WuiUsageDetail(BaseModel):
    item_id: str
    page: str
    days: int
    people: list[WuiUsagePerson]


def register_wui_usage_routes(
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

    @app.get("/admin/wui-usage.csv")
    async def wui_usage_raw(days: int = 30) -> Response:
        _require_superuser()
        days = _window(days)
        rows = await asyncio.to_thread(rows_in_window, spec, days)
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=RAW_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(raw_lines(rows))
        span = f"{days}d" if days else "all"
        name = f"wui-usage-{span}-{dt.datetime.now(dt.UTC).date().isoformat()}.csv"
        # UTF-8 with a BOM: Excel opens it as UTF-8 only when it sees one.
        return Response(
            content=out.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/admin/wui-usage", response_model=WuiUsageReport)
    async def wui_usage(days: int = 30) -> WuiUsageReport:
        _require_superuser()
        days = _window(days)
        rows = await asyncio.to_thread(rows_in_window, spec, days)

        def lines() -> list[WuiUsagePage]:
            return [
                WuiUsagePage(
                    item_id=line.item_id,
                    page=line.page,
                    title=locator.title_of(line.item_id) or "",
                    slug=locator.slug_of(line.item_id) or "",
                    person_days=line.person_days,
                    **_kinds(line.cells),
                )
                for line in per_page(rows)
            ]

        return WuiUsageReport(days=days, pages=await asyncio.to_thread(lines))

    @app.get("/admin/wui-usage/detail", response_model=WuiUsageDetail)
    async def wui_usage_detail(item_id: str, page: str, days: int = 30) -> WuiUsageDetail:
        _require_superuser()
        days = _window(days)
        rows = await asyncio.to_thread(rows_in_window, spec, days, item_id=item_id)
        people = [
            WuiUsagePerson(user=p.user, person_days=p.person_days, opens=p.opens, **_kinds(p.cells))
            for p in per_person(rows, item_id, page)
        ]
        return WuiUsageDetail(item_id=item_id, page=page, days=days, people=people)
