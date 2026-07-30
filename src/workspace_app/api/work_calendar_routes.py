"""#615 P1: the deployment's work calendar over the wire.

GET is open to any signed-in user — the goal panel and the off-hours sweeper
both need to know when unattended work may run. PUT is superuser-only: this one
row decides when an autonomous agent is allowed to touch everybody's chats.

Input is validated here rather than at read time. The editor is free text (a
date-per-line textarea), and a typo that survives into storage would not fail
loudly — it would silently move somebody's off-hours window, which is the
failure mode nobody traces back to a calendar edit.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from specstar import SpecStar

from ..resources.work_calendar import (
    DEFAULT_WORKDAYS,
    OVERRIDE_VALUES,
    WorkCalendar,
    read_work_calendar,
    upsert_work_calendar,
)


class WorkCalendarBody(BaseModel):
    workdays: list[int] = DEFAULT_WORKDAYS
    overrides: dict[str, str] = {}


class WorkCalendarOut(BaseModel):
    workdays: list[int]
    overrides: dict[str, str]
    # No `editable` flag here on purpose: superuser status already reaches the
    # FE once, via `GET /me` (`useIsSuperuser`). Shipping the same truth twice
    # is how the two copies eventually disagree.


def _validated(body: WorkCalendarBody) -> WorkCalendar:
    """The body as a storable calendar, or a 400 naming the offending entry."""
    for day in body.workdays:
        if not 0 <= day <= 6:
            raise HTTPException(
                status_code=400, detail=f"workday {day} is not a weekday (Monday=0 … Sunday=6)"
            )
    for day_text, value in body.overrides.items():
        try:
            date.fromisoformat(day_text)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"'{day_text}' is not a YYYY-MM-DD date"
            ) from exc
        if value not in OVERRIDE_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"'{value}' is not one of {' / '.join(OVERRIDE_VALUES)} (on {day_text})",
            )
    return WorkCalendar(workdays=sorted(set(body.workdays)), overrides=dict(body.overrides))


def register_work_calendar_routes(
    app: FastAPI | APIRouter,
    spec: SpecStar,
    get_user_id: Callable[[], str],
    *,
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Mount the deployment's work-calendar read/write endpoints."""

    def _out(cal: WorkCalendar) -> WorkCalendarOut:
        return WorkCalendarOut(workdays=cal.workdays, overrides=cal.overrides)

    @app.get("/work-calendar")
    async def get_work_calendar() -> WorkCalendarOut:
        """The deployment's calendar. Never 404s — an unconfigured deployment
        reads as the default Monday-to-Friday calendar."""
        return _out(read_work_calendar(spec))

    @app.put("/work-calendar")
    async def put_work_calendar(body: WorkCalendarBody) -> WorkCalendarOut:
        """Replace the calendar (superuser only)."""
        me = get_user_id()
        if me not in superusers:
            raise HTTPException(
                status_code=403, detail="only a superuser can edit the work calendar"
            )
        cal = _validated(body)
        upsert_work_calendar(spec, cal, user=me)
        return _out(cal)
