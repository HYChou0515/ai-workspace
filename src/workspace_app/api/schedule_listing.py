"""Schedules, read the way the sweep reads them — for one item's panel and for
the overview of every item (`docs/plan-schedule-overview.md`).

`grade_file` is THE sequence that turns a schedules file into rows: parse, cap,
which workflows the item offers, which of those will not parse, the ledger, the
landing stamp. The per-item route and the overview both call it, so the two
pages cannot disagree about a row; `tests/api/test_schedules_route_parity.py`
holds the per-item route — and so this — to the sweep.

The overview reads through the facade, as the per-item route does: a time just
changed shows on the next fetch, not one mirror interval later. That costs one
liveness probe per WARM item listed (a globally cold item is read from the
durable snapshot and woken by nothing), the price of opening the item's panel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from ..perm.model import Verb
from ..resources import Conversation
from ..workflow.offered import offered_workflow_ids, unparsable_workflow
from ..workflow.run import WorkflowRun
from ..workflow.user_schedules import (
    SchedulePolicy,
    ScheduleView,
    last_window_lookup,
    schedule_key,
    schedule_views,
    usable_rows,
    utc_now,
)
from .locator import ItemLocator
from .schedule_index import ScheduleIndex

logger = logging.getLogger(__name__)


async def grade_file(
    files: WorkspaceFiles,
    *,
    spec: SpecStar,
    policy: SchedulePolicy,
    item_id: str,
    slug: str,
    profile: str,
    path: str,
    raw: str,
    indexed: bool,
    landed: int | None,
) -> tuple[list[ScheduleView], list[str]]:
    """Every row of the schedules file at ``path``, as the sweep will treat it,
    plus the file's own problems. ``raw`` is the file already decoded the
    sweep's way (`utf-8`, `replace`); ``indexed`` / ``landed`` are the index's
    answers, which the caller holds."""
    offered = await offered_workflow_ids(files.ls, item_id, slug=slug, profile=profile)
    # Which of the workflows the rows name will not run because their own file
    # does not parse — asked per distinct `run`, the sweep's own check.
    broken: dict[str, str] = {}
    for run in {row.run for row in usable_rows(raw)[0]} & set(offered):
        problem = await unparsable_workflow(files.read, item_id, run)
        if problem is not None:
            broken[run] = problem
    # One hop off the loop: the ledger reads inside are blocking specstar I/O.
    return await asyncio.to_thread(
        schedule_views,
        raw,
        offered=offered,
        now_utc=utc_now(),
        last_window=last_window_lookup(spec if policy.sweep_enabled else None, item_id, path),
        max_rows=policy.max_rows,
        enabled=policy.sweep_enabled,
        indexed=indexed,
        broken=broken,
        landed_ms=landed,
        key_of=schedule_key(item_id, path),
    )


class LastRun(BaseModel):
    run_id: str
    status: str
    started: int | None = None
    ended: int | None = None


def last_run_of(spec: SpecStar, trigger_id: str) -> LastRun | None:
    """The newest run of the schedule ``trigger_id`` — scheduled or "run now" —
    or None when it has never run. Two point reads: the schedule's own chat
    (`ItemLocator.chat_for_schedule` keys it on the schedule's identity) names
    the run it was last linked to (`settle_run_chat`), and that run says how it
    went. Blocking specstar I/O; call it off the loop."""
    try:
        chat = spec.get_resource_manager(Conversation).get(trigger_id).data
    except (ResourceIDNotFoundError, ResourceIsDeletedError):
        return None
    run_id = chat.run_id if isinstance(chat, Conversation) else None
    if not run_id:
        return None
    try:
        run = spec.get_resource_manager(WorkflowRun).get(run_id).data
    except (ResourceIDNotFoundError, ResourceIsDeletedError):
        return None
    if not isinstance(run, WorkflowRun):  # pragma: no cover - defensive
        return None
    return LastRun(run_id=run_id, status=str(run.status), started=run.started, ended=run.ended)


class OverviewRow(BaseModel):
    slug: str
    item_id: str
    item_title: str
    item_owner: str
    path: str
    index: int
    raw: Any
    problems: list[str]
    run: str = ""
    describe: str = ""
    runnable: bool = False
    next_at: str = ""
    due_now: bool = False
    tz: str = "UTC"
    known: bool = False
    run_problem: str = ""
    trigger_id: str = ""
    last_run: LastRun | None = None
    can_edit: bool = False
    can_run: bool = False


class OverviewFile(BaseModel):
    """A schedules file with problems of its own — a file that does not parse
    has no rows to carry them, and would otherwise be invisible here."""

    slug: str
    item_id: str
    item_title: str
    path: str
    problems: list[str]


class ScheduleOverview(BaseModel):
    enabled: bool
    rows: list[OverviewRow]
    files: list[OverviewFile]


def register_schedule_overview_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    files: WorkspaceFiles,
    locator: ItemLocator,
    index: ScheduleIndex,
    policy: SchedulePolicy,
) -> None:
    def _may(slug: str, item_id: str, verb: Verb) -> bool:
        """The locator's gate as a yes/no — any refusal is "no", as on the WUI
        overview: one item must never take the page down."""
        try:
            locator.require_access(slug, item_id, verb)
        except HTTPException:
            return False
        return True

    def _decide(item_id: str) -> tuple[str, str, str, bool, bool, str] | None:
        """Everything the listing needs to know about an item before reading
        its files, or None when the viewer may not see it. Blocking."""
        slug = locator.slug_of(item_id)
        if slug is None or not _may(slug, item_id, "read_meta"):
            return None
        title, owner = locator.title_owner_of(item_id) or ("", "")
        return (
            slug,
            title,
            owner,
            _may(slug, item_id, "edit_content"),
            _may(slug, item_id, "execute"),
            locator.profile_of(item_id),
        )

    @app.get("/schedules", response_model=ScheduleOverview)
    async def schedules_overview() -> ScheduleOverview:
        """Every schedule in every item the viewer may read (`read_meta`, the
        per-item route's gate), each row with its last run and its next.

        The index names the files — it may name one since deleted, never miss
        one that exists — so a file that is gone is skipped, and the sweep
        drops it from the index on its own next pass.
        """
        rows: list[OverviewRow] = []
        problem_files: list[OverviewFile] = []
        for item_id, paths, landed in await asyncio.to_thread(index.entries):
            decided = await asyncio.to_thread(_decide, item_id)
            if decided is None:
                continue
            slug, title, owner, can_edit, can_run, profile = decided
            for path in paths:
                try:
                    data = await files.read(item_id, path)
                except FileNotFound:
                    continue
                views, problems = await grade_file(
                    files,
                    spec=spec,
                    policy=policy,
                    item_id=item_id,
                    slug=slug,
                    profile=profile,
                    path=path,
                    raw=data.decode("utf-8", "replace"),
                    indexed=True,
                    landed=landed.get(path),
                )
                if problems:
                    problem_files.append(
                        OverviewFile(
                            slug=slug,
                            item_id=item_id,
                            item_title=title,
                            path=path,
                            problems=problems,
                        )
                    )
                for view in views:
                    last = (
                        await asyncio.to_thread(last_run_of, spec, view.trigger_id)
                        if view.trigger_id
                        else None
                    )
                    fields = msgspec.to_builtins(view)
                    assert isinstance(fields, dict)  # narrow for ty
                    fields.pop("next_run", None)
                    fields.pop("payload", None)
                    rows.append(
                        OverviewRow(
                            **fields,
                            slug=slug,
                            item_id=item_id,
                            item_title=title,
                            item_owner=owner,
                            path=path,
                            last_run=last,
                            can_edit=can_edit,
                            can_run=can_run,
                        )
                    )
        return ScheduleOverview(enabled=policy.sweep_enabled, rows=rows, files=problem_files)


__all__ = [
    "LastRun",
    "ScheduleOverview",
    "grade_file",
    "last_run_of",
    "register_schedule_overview_routes",
]
