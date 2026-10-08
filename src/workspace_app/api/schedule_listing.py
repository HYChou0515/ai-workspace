"""Schedules, read the way the sweep reads them — for one item's panel and for
the overview of every item (`docs/plan-schedule-overview.md`).

`grade_file` is THE sequence that turns a schedules file into rows: parse, cap,
which workflows the item offers, which of those will not parse, the ledger, the
landing stamp. The per-item route and the overview both call it, so the two
pages cannot disagree about a row; `tests/api/test_schedules_route_parity.py`
holds the per-item route — and so this — to the sweep.

Listing reads go through the facade WITHOUT waking (`wake=False`): a live
sandbox answers, so a time just changed shows on the next fetch; an item with
no live sandbox — never given one, or reaped (nothing newer than its durable
copy: the app's reap writes back first, a host-side reap leaves the last
checkpoint) — is read from the durable copy and rebuilt by nothing. A file the live
workspace cannot see (still restoring) is looked for in the durable copy before
it is skipped (`read_schedules_file`). Every file op — the schedules file,
the `.workflows/` listing, each workflow the file names — resolves the item's
sandbox first: on http, an address read when this pod holds no session for
the item (published or not), then a liveness probe when there is an address or
a session; on kind: local, a liveness probe for every item. Actions (edit, remove, run
now) are about one item somebody pressed, and use ordinary reads.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from ..perm.model import Verb
from ..resources import Conversation
from ..workflow.offered import (
    no_such_workflow,
    offered_workflow_ids,
    profile_workflow_titles,
    read_workflow_file,
    unparsable_workflow,
    wont_parse,
)
from ..workflow.orchestrator import ActiveRunExists
from ..workflow.run import WorkflowRun
from ..workflow.user_schedules import (
    ITEM_SCHEDULES_PATH,
    SchedulePolicy,
    ScheduleView,
    UserSchedule,
    last_window_lookup,
    parse_row,
    schedule_key,
    schedule_views,
    usable_rows,
    utc_now,
)
from .file_routes import _workspace_path
from .locator import ItemLocator
from .schedule_index import ScheduleIndex, is_schedule_file
from .wui_deploy import DeployedWui

logger = logging.getLogger(__name__)

_CHANGED = "This schedule has changed since it was listed — reload and try again."
_UNREADABLE = "This schedules file could not be read just now."


ReadFile = Callable[[str, str], Awaitable[bytes]]
ListFiles = Callable[..., Awaitable[list[str]]]


class Source(NamedTuple):
    """Where a schedules file was read from — and so where everything graded
    against it is read from too, one copy for the whole verdict."""

    read: ReadFile
    ls: ListFiles


async def read_schedules_file(
    files: WorkspaceFiles, durable: Source, item_id: str, path: str
) -> tuple[bytes, Source] | None:
    """The schedules file at ``path`` and the copy it came from, or None when
    neither copy has it.

    The live workspace first, so a time just changed shows at once. When the
    live workspace says "not there", the DURABLE copy — the one the sweep reads
    — before believing it: a sandbox that exists but has not finished restoring
    answers "not there" for every file (`WorkspaceFiles._warm` asks whether the
    sandbox exists, never whether it is ready), and starting a run is what puts
    an item there. Without this, the refetch after Run now landed in that window
    and the overview said nothing was scheduled.
    """

    # Never waking: a listing reads many items, and on a host-managed deploy a
    # read that may wake rebuilds every idle-reaped sandbox it touches. A
    # sandbox that is gone has nothing newer than its durable copy (the app's
    # reap writes back first; a host-side reap leaves the last checkpoint,
    # which is all a rebuild would restore) — `WorkspaceFiles._warm`.
    async def _read(workspace_id: str, rel: str) -> bytes:
        return await files.read(workspace_id, rel, wake=False)

    async def _ls(workspace_id: str, prefix: str = "") -> list[str]:
        return await files.ls(workspace_id, prefix, wake=False)

    live = Source(read=_read, ls=_ls)
    try:
        return await _read(item_id, path), live
    except FileNotFound:
        pass
    try:
        return await durable.read(item_id, path), durable
    except (FileNotFound, FileNotFoundError):
        return None


async def grade_file(
    source: Source,
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
    sweep's way (`utf-8`, `replace`); ``source`` is the copy it was read from,
    which the workflows it names are read from too; ``indexed`` / ``landed``
    are the index's answers, which the caller holds."""
    offered = await offered_workflow_ids(source.ls, item_id, slug=slug, profile=profile)
    # Which of the workflows the rows name will not run because their own file
    # does not parse — asked per distinct `run`, the sweep's own check — and,
    # from the same read, what each is called. The item's own file shadows
    # the profile's workflow of the same id, as it does when it runs.
    broken: dict[str, str] = {}
    titles = profile_workflow_titles(slug, profile)
    for run in {row.run for row in usable_rows(raw)[0]} & set(offered):
        found = await read_workflow_file(source.read, item_id, run)
        if found is None:
            continue
        titles[run] = found.title
        if found.problem is not None:
            broken[run] = found.problem
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
        titles=titles,
    )


def without_payload(raw: Any) -> Any:
    """A row as a viewer who may not read the item's files sees it: everything
    but `with`. `read_meta` lists a schedule; what it sends to its workflow is
    file content, which `read_content` guards (`GET /files/...` refuses that
    viewer). The time, the workflow and the identity stay — the row is still
    described, and still removable — by its position and this value — by
    someone who may edit (`RowRef.index`)."""
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if k != "with"}
    return raw


class LastRun(BaseModel):
    run_id: str
    status: str
    started: int | None = None
    ended: int | None = None
    by_hand: bool = False
    """Started by "run now", not by the schedule's time (`WorkflowRun.by_hand`)."""


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
    return LastRun(
        run_id=run_id,
        status=str(run.status),
        started=run.started,
        ended=run.ended,
        by_hand=run.by_hand,
    )


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
    next_ms: int | None = None
    due_now: bool = False
    tz: str = "UTC"
    known: bool = False
    run_problem: str = ""
    trigger_id: str = ""
    last_run: LastRun | None = None
    can_edit: bool = False
    can_run: bool = False
    can_read: bool = False
    """Whether the viewer may read the item's files: without it a row comes
    without its `with`, and a refused row is matched for Remove against the
    value as shown — without `with` (`RowRef.index`)."""
    page_path: str = ""
    """The Deployed view file in this schedules file's folder — where Open
    goes for a page's row (the WUI overview's address). "" for the item's own
    schedules or a page never Deployed: Open goes to the item."""
    page_title: str = ""
    """That page's title as Deployed — what a person calls the page; "" when
    it has none (or there is no page): the folder names it instead."""
    run_title: str = ""
    """The title of the workflow `run` names (`ScheduleView.run_title`)."""


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


class ScheduleAction(BaseModel):
    """What an action answers: the row's identity after it (a new time is a
    new identity)."""

    trigger_id: str
    run_id: str = ""


class RowRef(BaseModel):
    """Which row: the file, and the row's identity in it. The server re-reads
    the file and finds the row by identity, so an action never applies to
    bytes the viewer did not see — a row changed meanwhile is a 409."""

    path: str
    trigger_id: str
    raw: Any = None
    """For Remove only, and only when `trigger_id` is "": the row's value AS
    THIS VIEWER WAS SHOWN IT (without `with` for one who may not read the
    files). A row the sweep refuses has no identity — it never fires — but a
    person must still be able to take it out of the file."""
    index: int | None = None
    """With `raw`: the row's position when it was listed. Values alone are not
    unique — two refused rows can differ only in their `with`, which a viewer
    without `read_content` cannot see — so the row is named by position and
    checked against the value, and a mismatch is "changed since listed"."""


class EditTime(RowRef):
    """A new time: `every` and the fields it reads, or a `cron` — one of the two
    (`docs/plan-schedule-cron.md` decision 4)."""

    every: str = ""
    cron: str = ""
    n: int = 0
    at: str = ""
    dow: str = ""
    dom: int = 0
    tz: str = ""


#: The fields "edit time" may change (decision 8). `run` and `with` are not
#: among them: changing what runs is a different act from changing when.
_TIME_KEYS = ("every", "n", "at", "dow", "dom", "tz", "cron")


def _time_fields(body: EditTime) -> dict[str, Any]:
    """The new time as a row writes it — only the fields this `every` reads, so
    a daily row edited to weekly does not keep a `dom` nothing consults. A cron
    is the whole "when": with it only the zone stays."""
    if body.cron:
        return {"cron": body.cron, **({"tz": body.tz} if body.tz else {})}
    out: dict[str, Any] = {"every": body.every}
    if body.every == "minutes":
        out["n"] = body.n
    if body.every in ("daily", "weekly", "monthly"):
        out["at"] = body.at
    if body.every == "weekly":
        out["dow"] = body.dow
    if body.every == "monthly":
        out["dom"] = body.dom
    if body.tz:
        out["tz"] = body.tz
    return out


def register_schedule_overview_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    files: WorkspaceFiles,
    locator: ItemLocator,
    index: ScheduleIndex,
    policy: SchedulePolicy,
    get_user_id: Callable[[], str],
    start_run: Callable[..., Awaitable[str | None]],
    durable: Source,
    on_saved: Callable[[str, str], None],
    deployed_pages: Callable[[], list[DeployedWui]] = list,
) -> None:
    """``start_run`` is the sweep's own start (`start_page_schedule`, bound in
    `create_app`): "run now" goes through exactly what a fire goes through —
    the schedule's chat, its one-run-at-a-time rule — only now, as the
    presser. ``on_saved(item, path)`` runs after an edit or remove lands: what
    the file PUT does after a save (an activity entry, a `FileChanged` so other
    viewers of the file refetch rather than save the old rows back)."""

    def _may(slug: str, item_id: str, verb: Verb) -> bool:
        """The locator's gate as a yes/no — any refusal is "no", as on the WUI
        overview: one item must never take the page down."""
        try:
            locator.require_access(slug, item_id, verb)
        except HTTPException:
            return False
        return True

    def _decide(item_id: str) -> tuple[str, str, str, bool, bool, bool, str] | None:
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
            _may(slug, item_id, "read_content"),
            locator.profile_of(item_id),
        )

    async def _read_doc(item_id: str, ref: RowRef) -> tuple[str, dict[str, Any], list[Any]]:
        """The file at ``ref.path`` as (path, document, its rows). 400 for a
        path that is not a schedules file; 409 for one that is gone or no
        longer a schedules document — it changed since the viewer saw it."""
        path = _workspace_path(ref.path)
        if not is_schedule_file(path):
            raise HTTPException(status_code=400, detail=f"not a schedules file: {path}")
        try:
            raw = (await files.read(item_id, path)).decode("utf-8", "replace")
        except FileNotFound:
            raise HTTPException(status_code=409, detail=_CHANGED) from None
        try:
            doc = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=409, detail=_CHANGED) from None
        rows = doc.get("schedules") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            raise HTTPException(status_code=409, detail=_CHANGED)
        return path, doc, rows

    async def _locate(item_id: str, ref: RowRef) -> tuple[str, dict[str, Any], int, UserSchedule]:
        """The row whose identity is ``ref.trigger_id``: (path, document, row
        index, parsed row); 409 when no row has that identity any more."""
        path, doc, rows = await _read_doc(item_id, ref)
        key_of = schedule_key(item_id, path)
        for i, raw_row in enumerate(rows):
            row, _ = parse_row(i, raw_row)
            if row is not None and key_of(row) == ref.trigger_id:
                return path, doc, i, row
        raise HTTPException(status_code=409, detail=_CHANGED)

    async def _save(item_id: str, path: str, doc: dict[str, Any]) -> None:
        # Through the facade: indexed and stamped by the write hook like every
        # other save, so the birth rule sees the edit (no catch-up for it).
        # `ensure_ascii=False`: a neighbouring row's 品管課 stays 品管課, not
        # `\\u54c1…` — the file is read by people and agents too.
        await files.write(item_id, path, json.dumps(doc, indent=2, ensure_ascii=False).encode())
        on_saved(item_id, path)

    @app.post("/a/{slug}/items/{item_id}/schedules/edit", response_model=ScheduleAction)
    async def edit_schedule_time(slug: str, item_id: str, body: EditTime) -> ScheduleAction:
        """Move one schedule to a new time. Only the time — `run` and its `with`
        stay as written — and only if the row the sweep would read is valid:
        refused with the sweep's own sentence otherwise, the file untouched.

        A new time is a new schedule identity (`trigger_id_for` hashes it), so
        its ledger, its chat and any "run as me" binding start over; the
        landing stamp keeps it from catching up on a window that passed before
        the edit (docs/plan-schedule-overview.md decisions 10, 16)."""
        workspace_id = locator.require_access(slug, item_id, "edit_content")
        if bool(body.every) == bool(body.cron):
            raise HTTPException(
                status_code=422, detail="A new time says either `every` or `cron` — one of the two."
            )
        path, doc, i, _row = await _locate(workspace_id, body)
        edited = {k: v for k, v in doc["schedules"][i].items() if k not in _TIME_KEYS}
        edited = {**_time_fields(body), **edited}
        parsed, problems = parse_row(i, edited)
        if parsed is None:
            raise HTTPException(status_code=422, detail=" ".join(problems))
        doc["schedules"][i] = edited
        await _save(workspace_id, path, doc)
        return ScheduleAction(trigger_id=schedule_key(workspace_id, path)(parsed))

    @app.post("/a/{slug}/items/{item_id}/schedules/remove", status_code=204)
    async def remove_schedule(slug: str, item_id: str, body: RowRef) -> Response:
        """Drop one schedule from its file — by identity, or, for a row the
        sweep refuses, by its position and its value as this viewer was shown it
        (`RowRef.index`); every other row stays as written, the refused ones
        included."""
        workspace_id = locator.require_access(slug, item_id, "edit_content")
        if body.trigger_id:
            path, doc, i, _row = await _locate(workspace_id, body)
        else:
            # A row the sweep refuses has no identity: it is named by its
            # position and checked against its value as this viewer was shown
            # it (review round 2 — by value alone, a viewer who sees rows
            # without `with` matched, and deleted, a different row). JSON
            # equality: an object's key order does not matter, a list's does.
            path, doc, rows = await _read_doc(workspace_id, body)
            i = body.index if body.index is not None else -1
            shown = _may(slug, item_id, "read_content")
            if not 0 <= i < len(rows):
                raise HTTPException(status_code=409, detail=_CHANGED)
            if (rows[i] if shown else without_payload(rows[i])) != body.raw:
                raise HTTPException(status_code=409, detail=_CHANGED)
        del doc["schedules"][i]
        await _save(workspace_id, path, doc)
        return Response(status_code=204)

    @app.post("/a/{slug}/items/{item_id}/schedules/run", status_code=202)
    async def run_schedule_now(slug: str, item_id: str, body: RowRef) -> ScheduleAction:
        """Run one schedule now (decision 6): its workflow and its `with`, in its
        own chat, as the presser; the ledger is not touched, so its next time is
        unchanged. Refused while its previous run is still going — the rule a
        fire obeys, both ways: if THIS run is still going when the next time
        comes, that fire is skipped as an overrun (`UserScheduleSweeper`).
        `execute`, the verb of the other run that carries a payload (`wui/run`)
        and of the schedule's "run as me" binding."""
        workspace_id = locator.require_access(slug, item_id, "execute")
        _path, _doc, _i, row = await _locate(workspace_id, body)
        offered = await offered_workflow_ids(
            files.ls, workspace_id, slug=slug, profile=locator.profile_of(workspace_id)
        )
        if row.run not in offered:
            raise HTTPException(status_code=403, detail=no_such_workflow(row.run, offered))
        problem = await unparsable_workflow(files.read, workspace_id, row.run)
        if problem is not None:
            raise HTTPException(status_code=422, detail=wont_parse(row.run, problem))
        presser = get_user_id()
        try:
            run_id = await start_run(
                item_id=workspace_id,
                workflow_id=row.run,
                acting_user=presser,
                payload=row.payload,
                key=body.trigger_id,
                env_user=presser,
                by_hand=True,
            )
        except ActiveRunExists:
            raise HTTPException(
                status_code=409,
                detail="This schedule's previous run is still going (or waiting for a "
                "review) — it can run again once that one finishes.",
            ) from None
        return ScheduleAction(trigger_id=body.trigger_id, run_id=run_id or "")

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
        # Deployed pages by (item, folder): one listing for the whole page.
        # Newest Deploy first, so the first page seen for a folder is the one
        # kept when a folder holds two Deployed view files.
        pages: dict[tuple[str, str], DeployedWui] = {}
        for page in await asyncio.to_thread(deployed_pages):
            pages.setdefault((page.item_id, page.path.rsplit("/", 1)[0]), page)
        for item_id, paths, landed in await asyncio.to_thread(index.entries):
            decided = await asyncio.to_thread(_decide, item_id)
            if decided is None:
                continue
            slug, title, owner, can_edit, can_run, can_read, profile = decided

            def _file(
                path: str, problems: list[str], slug=slug, item_id=item_id, title=title
            ) -> OverviewFile:
                return OverviewFile(
                    slug=slug, item_id=item_id, item_title=title, path=path, problems=problems
                )

            for path in paths:
                try:
                    found = await read_schedules_file(files, durable, item_id, path)
                    if found is None:
                        continue
                    data, source = found
                    views, problems = await grade_file(
                        source,
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
                    lasts = [
                        await asyncio.to_thread(last_run_of, spec, view.trigger_id)
                        if view.trigger_id
                        else None
                        for view in views
                    ]
                except Exception:  # noqa: BLE001 — one file must not take the page down
                    # Anything but "not there", which `read_schedules_file`
                    # already answers: a path that became a folder, a host
                    # error. Said for that file; every other file still lists.
                    logger.exception("schedules overview: %s %s could not be read", item_id, path)
                    problem_files.append(_file(path, [_UNREADABLE]))
                    continue
                if problems:
                    problem_files.append(_file(path, problems))
                page = (
                    pages.get((item_id, path.rsplit("/", 1)[0]))
                    if path != ITEM_SCHEDULES_PATH
                    else None
                )
                for view, last in zip(views, lasts, strict=True):
                    fields = msgspec.to_builtins(view)
                    assert isinstance(fields, dict)  # narrow for ty
                    fields.pop("next_run", None)
                    fields.pop("payload", None)
                    if not can_read:
                        fields["raw"] = without_payload(fields["raw"])
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
                            can_read=can_read,
                            page_path=page.path if page is not None else "",
                            page_title=page.title if page is not None else "",
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
