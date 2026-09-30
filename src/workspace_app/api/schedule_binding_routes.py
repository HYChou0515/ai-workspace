""" "Run as me" — the routes behind a schedule's binding
(`docs/plan-wui-viewer-login.md` Q7/Q8/Q12; the store is
`workflow.schedule_bindings`).

``GET    …/schedule-bindings?path=``          a schedules file's rows, each with who it runs as
``PUT    …/schedule-bindings/{key}?path=``    bind that schedule to the CALLER
``DELETE …/schedule-bindings/{key}?path=``    take the caller's own binding off it

Binding needs ``execute`` — the verb that lets a person make this item run
work (`wui/run`), so no one gains a way to start anything they could not
start already. The binder is always the caller: there is no parameter by which
to bind someone else. Replacing another person's binding tells them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel
from specstar import SpecStar

from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from ..workflow.schedule_bindings import ScheduleBindings
from ..workflow.user_schedules import describe_row, trigger_id_for, usable_rows
from ..workflow.workspace_store import SCHEDULES_FILE
from .file_routes import _workspace_path
from .locator import ItemLocator
from .notifications import notify


class BindingRow(BaseModel):
    trigger_id: str
    run: str
    describe: str
    #: Who the schedule runs as; "" = nobody (the shared layer only).
    bound_to: str
    #: Whether that is the caller — the one fact the UI needs to offer
    #: "stop running as me" rather than "run as me".
    mine: bool


class Bindings(BaseModel):
    rows: list[BindingRow]


def register_schedule_binding_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    files: WorkspaceFiles,
    bindings: ScheduleBindings,
    get_user_id: Callable[[], str],
) -> None:
    def _schedules_path(raw: str) -> str:
        path = _workspace_path(raw)
        if path.rsplit("/", 1)[-1] != SCHEDULES_FILE:
            raise HTTPException(status_code=400, detail=f"not a {SCHEDULES_FILE}: {path}")
        return path

    async def _keys(workspace_id: str, path: str) -> list[tuple[str, BindingRow]]:
        """Each usable row's key, computed the way the SWEEP computes it
        (`user_schedule_sweep._one_file`: folder = the file's directory), so a
        binding made here is the one the sweep looks up."""
        try:
            raw = (await files.read(workspace_id, path)).decode("utf-8", "replace")
        except FileNotFound:
            return []
        folder = path.rsplit("/", 1)[0]
        out: list[tuple[str, BindingRow]] = []
        for row in usable_rows(raw)[0]:
            key = trigger_id_for(workspace_id, folder, row)
            out.append(
                (
                    key,
                    BindingRow(
                        trigger_id=key,
                        run=row.run,
                        describe=describe_row(row),
                        bound_to="",
                        mine=False,
                    ),
                )
            )
        return out

    @app.get("/a/{slug}/items/{item_id}/schedule-bindings", response_model=Bindings)
    async def list_schedule_bindings(slug: str, item_id: str, path: str) -> Bindings:
        workspace_id = locator.require_access(slug, item_id, "read_content")
        me = get_user_id()
        rows: list[BindingRow] = []
        for key, row in await _keys(workspace_id, _schedules_path(path)):
            who = await asyncio.to_thread(bindings.binder, key)
            rows.append(row.model_copy(update={"bound_to": who, "mine": bool(who) and who == me}))
        return Bindings(rows=rows)

    @app.put("/a/{slug}/items/{item_id}/schedule-bindings/{trigger_id}", response_model=BindingRow)
    async def bind_schedule(slug: str, item_id: str, trigger_id: str, path: str) -> BindingRow:
        workspace_id = locator.require_access(slug, item_id, "execute")
        file_path = _schedules_path(path)
        found = dict(await _keys(workspace_id, file_path)).get(trigger_id)
        if found is None:
            # The key names CONTENT; one the file does not hold is a schedule
            # that was edited or removed since the page listed it.
            raise HTTPException(
                status_code=404, detail="that schedule has changed — reload the page"
            )
        me = get_user_id()
        replaced = await asyncio.to_thread(
            bindings.bind, trigger_id, item_id=workspace_id, path=file_path, user_id=me
        )
        if replaced:
            await asyncio.to_thread(
                notify,
                spec,
                recipient=replaced,
                kind="schedule_binding_replaced",
                title="A schedule no longer runs as you",
                body=(
                    f"{me} chose to run “{found.run}” ({found.describe}) in {file_path} "
                    "with their own values instead of yours."
                ),
                actor=me,
            )
        return found.model_copy(update={"bound_to": me, "mine": True})

    @app.delete("/a/{slug}/items/{item_id}/schedule-bindings/{trigger_id}", status_code=204)
    async def unbind_schedule(slug: str, item_id: str, trigger_id: str, path: str) -> Response:
        """Take one's OWN name off a schedule. Someone else's binding is theirs
        to withdraw — replacing it (PUT) is the way to change it, and that tells
        them."""
        workspace_id = locator.require_access(slug, item_id, "read_content")
        _schedules_path(path)
        found = await asyncio.to_thread(bindings.get, trigger_id)
        if found is None:
            return Response(status_code=204)  # already nobody's — the state asked for
        if found.item_id != workspace_id:
            raise HTTPException(status_code=404, detail="no such schedule in this item")
        if found.user_id != get_user_id():
            raise HTTPException(status_code=403, detail="this schedule runs as someone else")
        await asyncio.to_thread(bindings.unbind, trigger_id)
        return Response(status_code=204)
