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
from ..workflow.offered import unparsable_workflow, wont_parse
from ..workflow.schedule_bindings import ScheduleBindings, file_keys, workflow_digest
from ..workflow.user_schedules import describe_row
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
    #: Why it runs as `bound_to` (`plan-schedule-run-as` G8): "self" / "author"
    #: / "admin"; "" when it runs as nobody.
    reason: str = ""
    #: The superuser who named `bound_to`, when `reason` is "admin".
    assigned_by: str = ""


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
    may_run: Callable[[str, str], bool] = lambda _user, _item: False,
    superusers: frozenset[str] = frozenset(),
) -> None:
    """``may_run(user, item)`` is the `execute` rule the sweep re-checks before
    each fire — asked of the person a superuser names (G5), who is not the
    caller and so has passed no gate of this request."""

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
        out: list[tuple[str, BindingRow]] = []
        for key, row in file_keys(workspace_id, path, raw):
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
            found = await asyncio.to_thread(bindings.get, key)
            who = found.user_id if found is not None else ""
            rows.append(
                row.model_copy(
                    update={
                        "bound_to": who,
                        "mine": bool(who) and who == me,
                        "reason": found.reason if found is not None else "",
                        "assigned_by": found.assigned_by if found is not None else "",
                    }
                )
            )
        return Bindings(rows=rows)

    @app.put("/a/{slug}/items/{item_id}/schedule-bindings/{trigger_id}", response_model=BindingRow)
    async def bind_schedule(
        slug: str, item_id: str, trigger_id: str, path: str, user: str = ""
    ) -> BindingRow:
        """Run the schedule as the CALLER ("Run as me") — or, for a superuser,
        as ``user`` (`plan-schedule-run-as` G5): only the deployment's
        superusers may lend someone else's values, and only someone who may
        run work in the item; that person is told (G6)."""
        workspace_id = locator.require_access(slug, item_id, "execute")
        me = get_user_id()
        who = user or me
        naming = who != me
        if naming and me not in superusers:
            raise HTTPException(
                status_code=403,
                detail="only an administrator can choose someone else to run a schedule as",
            )
        if naming and not await asyncio.to_thread(may_run, who, workspace_id):
            raise HTTPException(
                status_code=422,
                detail=f"{who} cannot run work in this item, so it cannot run as them",
            )
        file_path = _schedules_path(path)
        found = dict(await _keys(workspace_id, file_path)).get(trigger_id)
        if found is None:
            # The key names CONTENT; one the file does not hold is a schedule
            # that was edited or removed since the page listed it.
            raise HTTPException(
                status_code=404, detail="that schedule has changed — reload the page"
            )
        # Nothing to consent to while the file will not parse (round 6): the
        # consent would record "", the profile workflow's digest, and a later
        # delete would let that workflow run with this person's values.
        problem = await unparsable_workflow(files.read, workspace_id, found.run)
        if problem is not None:
            raise HTTPException(status_code=422, detail=wont_parse(found.run, problem))
        # The workflow as it is NOW is part of what is consented to (F4): the
        # sweep drops the binding when that file's body changes.
        digest = await workflow_digest(files.read, workspace_id, found.run)
        replaced = await asyncio.to_thread(
            bindings.bind,
            trigger_id,
            item_id=workspace_id,
            path=file_path,
            user_id=who,
            workflow_digest=digest,
            reason="admin" if naming else "self",
            assigned_by=me if naming else "",
        )
        if naming:
            await asyncio.to_thread(
                notify,
                spec,
                recipient=who,
                kind="schedule_binding_assigned",
                title="A schedule now runs as you",
                body=(
                    f"An administrator, {me}, chose to run “{found.run}” ({found.describe}) "
                    f"in {file_path} with your own sign-ins and values. If you do not want "
                    "that, open the schedule and choose “Stop running as me”."
                ),
                actor=me,
            )
        if replaced and replaced != who:
            await asyncio.to_thread(
                notify,
                spec,
                recipient=replaced,
                kind="schedule_binding_replaced",
                title="A schedule no longer runs as you",
                body=(
                    f"{me} chose to run “{found.run}” ({found.describe}) in {file_path} "
                    + (
                        f"with {who}'s values instead of yours."
                        if naming
                        else "with their own values instead of yours."
                    )
                ),
                actor=me,
            )
        return found.model_copy(
            update={
                "bound_to": who,
                "mine": not naming,
                "reason": "admin" if naming else "self",
                "assigned_by": me if naming else "",
            }
        )

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
