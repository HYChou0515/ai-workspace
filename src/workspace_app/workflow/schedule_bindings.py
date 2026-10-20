"""Who a page schedule runs AS (`docs/plan-wui-viewer-login.md` Q7/Q8).

A schedule has no request behind it, so by default its tools get the shared
layer and the deploy's request-less answer only — nobody's private values. A
person may lend theirs by pressing "run as me": that writes a binding here, and
from then on the schedule's fires carry that person's private layer.

Three rules, each for a reason:

* **The binding is to what was consented to: the ROW and the WORKFLOW it
  names.** The key is derived from the row's content
  (`user_schedules.trigger_id_for`), so an edited row is a different schedule
  with no binding; and the binding records a digest of the item's own
  ``.workflows/<run>.json`` (`workflow_digest`), so rewriting the workflow's
  body drops it too (review round 1, F4). The sweep compares against the LIVE
  file — the one the fire will load — before each fire (round 2, D3), drops a
  binding that no longer matches, and says so to the binder. The digest covers
  that one file only: a script the workflow calls, or a file an agent node
  reads, is not part of what was consented to, and anyone who may edit the
  item's content may change those (round 2 veracity, V1).
* **A binder who may no longer run work in the item** (removed, demoted) has
  the binding dropped at the next fire, and is told why.
* **One person per schedule** (Q8). Another may take it over; the one replaced
  is told. Per-person fan-out would multiply every fire by its subscribers.
* **Only the person themself can create a binding in their name** — the route
  binds the CALLER. Whoever writes `schedules.json` (any `edit_content` holder,
  the page, the agent) chooses nothing about whose values it runs with.

Registered post-``spec.apply``: no auto-CRUD, so the routes are the only door.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable

from msgspec import Struct
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from .user_schedules import UserSchedule, file_rows, over_cap, trigger_id_for, usable_rows
from .workspace_store import load_workspace_workflow_digested

logger = logging.getLogger(__name__)


async def workflow_digest(
    read: Callable[[str, str], Awaitable[bytes]], item_id: str, workflow_id: str
) -> str:
    """What consenting to ``workflow_id`` in this item is recorded as: the
    digest the BUILD would run from (`load_workspace_workflow_digested`) — ""
    when it would run a profile's workflow (no file, one that will not parse,
    the reserved id). Defined BY the loader, so a binding, a press and a
    built interpreter cannot disagree (round 5: a broken file digested as its
    bytes here and as "" at the build). A read that fails otherwise RAISES:
    the caller decides what an unknown means."""
    built = await load_workspace_workflow_digested(read, item_id, workflow_id)
    return built[2] if built is not None else ""


class ScheduleBinding(Struct):
    trigger_id: str
    item_id: str
    #: The schedules file the row lives in — the sweep compares a binding only
    #: against the file it came from.
    path: str
    user_id: str
    bound_at: int
    #: `workflow_digest` of the row's workflow when the binder pressed "Run as
    #: me" — the digest the build would run from ("" means a profile's
    #: workflow; a file that will not parse cannot be bound). The row's key only
    #: covers the ROW; the workflow it names can be rewritten by anyone who edits
    #: the item, and the binder consented to it as it was (review round 1, F4).
    workflow_digest: str = ""
    #: WHY it runs as `user_id` (`plan-schedule-run-as` G8): ``"self"`` — they
    #: pressed "Run as me" (every binding made before this field existed);
    #: ``"author"`` — they wrote the row (G2); ``"admin"`` — a superuser named
    #: them (G5). Shown, never consulted by the sweep: whose values a fire
    #: carries is `user_id` alone, whatever put it there.
    reason: str = "self"
    #: The superuser who named them, for ``reason == "admin"``; "" otherwise.
    assigned_by: str = ""


def register_schedule_bindings(spec: SpecStar) -> None:
    with contextlib.suppress(ValueError):
        spec.add_model(ScheduleBinding, indexed_fields=["item_id"])


class ScheduleBindings:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _rm(self):
        return self._spec.get_resource_manager(ScheduleBinding)

    def get(self, trigger_id: str) -> ScheduleBinding | None:
        try:
            data = self._rm().get(trigger_id).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, ScheduleBinding)
        return data

    def binder(self, trigger_id: str) -> str:
        """Who the schedule runs as, or "" for nobody."""
        found = self.get(trigger_id)
        return found.user_id if found is not None else ""

    def bind(
        self,
        trigger_id: str,
        *,
        item_id: str,
        path: str,
        user_id: str,
        workflow_digest: str = "",
        reason: str = "self",
        assigned_by: str = "",
    ) -> str:
        """Bind the schedule to ``user_id``, replacing whoever held it. Returns
        the replaced person ("" when there was none, or it was them already)."""
        # Imported here: `api/__init__` builds the app, which imports this
        # module — a module-level import made it a cycle for any reader that
        # reached this module first.
        from ..api.timeutil import now_ms

        row = ScheduleBinding(
            trigger_id=trigger_id,
            item_id=item_id,
            path=path,
            user_id=user_id,
            bound_at=now_ms(),
            workflow_digest=workflow_digest,
            reason=reason,
            assigned_by=assigned_by,
        )
        previous = self.get(trigger_id)
        if previous is None:
            self._rm().create(row, resource_id=trigger_id)
            return ""
        self._rm().update(trigger_id, row)
        return previous.user_id if previous.user_id != user_id else ""

    def unbind(self, trigger_id: str) -> None:
        with contextlib.suppress(ResourceIDNotFoundError):
            self._rm().permanently_delete(trigger_id)

    def for_item(self, item_id: str) -> list[ScheduleBinding]:
        rows: list[ScheduleBinding] = []
        for res in self._rm().list_resources((QB["item_id"] == item_id).build()):
            data = res.data
            assert isinstance(data, ScheduleBinding)
            rows.append(data)
        return rows

    def holders(self, item_id: str, path: str) -> set[str]:
        """Who runs some row of the schedules file ``path``."""
        return {b.user_id for b in self.for_item(item_id) if b.path == path}

    def purge_item(self, item_id: str) -> None:
        """Every binding of one item — the item-delete cascade's step."""
        for b in self.for_item(item_id):
            self.unbind(b.trigger_id)

    def expire_absent(self, item_id: str, path: str, live: set[str]) -> list[ScheduleBinding]:
        """Drop the bindings of ``path`` whose key the file no longer holds, and
        return them so their binders can be told."""
        gone = [b for b in self.for_item(item_id) if b.path == path and b.trigger_id not in live]
        for b in gone:
            self.unbind(b.trigger_id)
        return gone


def file_keys(item_id: str, path: str, raw: str) -> list[tuple[str, UserSchedule]]:
    """``(key, row)`` of every row of the schedules file at ``path`` the sweep
    would fire — keyed the way the SWEEP keys it (`user_schedule_sweep._one_file`:
    folder = the file's directory), so a binding made from these is the one a
    fire looks up. The one definition the binding routes and the crediting
    both read through."""
    folder = path.rsplit("/", 1)[0]
    return [(trigger_id_for(item_id, folder, row), row) for row in usable_rows(raw)[0]]


def _unreadable(raw: str) -> bool:
    """Whether a schedules file HID rows nobody can name: rows the sweep could
    not read — text that is not JSON, or JSON that holds a row somewhere other
    than a `schedules` list (a mistyped key, a bare list; review round 3). A
    file that holds no row at all hides nothing, whatever its shape: blank
    (the file tree's New file writes "" before the first save — round 2),
    `{}`, `{"schedules": null}`, `[]`, `{"schedule": []}`, `null` (round 4)."""
    if not raw.strip():
        return False
    try:
        doc = json.loads(raw)
    except ValueError:
        return True
    if file_rows(raw) is not None:
        return False
    return _holds_a_row(doc)


def _holds_a_row(doc: object) -> bool:
    """Whether decoded JSON has any non-empty list or object in it — the only
    places a row can be. A top-level object's own keys are not rows; its values
    may hold them."""
    values = doc.values() if isinstance(doc, dict) else [doc]
    return any(isinstance(v, (list, dict)) and len(v) > 0 for v in values)


class ScheduleAuthorship:
    """`plan-schedule-run-as` G2: a row runs as the person who WROTE it.

    Called by the doors that know who is writing — the agent's
    `save_schedules` / `write_file` / `edit_file`, the file PUT route (the
    editor, a page's `writeFile`, an upload, the panel's import), the schedule
    "change time" route — with the file as it was before the write and as it
    is after. Every other way a row can appear (an agent's `exec`, a copy, a
    zip import, a seed, find-and-replace, a row older than this feature)
    passes through none of them, so it runs as nobody (G3) — no backfill.

    Only the rows THIS write added are credited (``after - before``). Saving
    the whole file again — `save_schedules` always does — must not claim the
    rows that were already there: an older row nobody wrote, or one whose
    person chose "stop running as me", would otherwise land in the name of
    whoever touched the file next.

    A row is credited only when nobody holds it yet, the author may run work
    in the item (``may_run`` — `execute`, the "Run as me" verb; G7), and its
    workflow parses: the binding records the workflow as it is now, the
    consent rule "Run as me" follows (`workflow_digest`)."""

    def __init__(
        self,
        bindings: ScheduleBindings,
        *,
        read: Callable[[str, str], Awaitable[bytes]],
        may_run: Callable[[str, str], bool],
        max_rows: int | None = None,
    ) -> None:
        self._bindings = bindings
        self._read = read
        self._may_run = may_run
        # The sweep's own cap: a file over it fires nothing, and the sweep does
        # not expire bindings while it stays over — crediting its rows would
        # only grow the table (review round 1, defect 2).
        self._max_rows = max_rows

    def runs_as(self, key: str) -> str:
        """Who the row ``key`` runs as now, or "" for nobody. Blocking."""
        return self._bindings.binder(key)

    async def credit(
        self, item_id: str, path: str, *, before: str | None, after: str, author: str
    ) -> list[str]:
        """Bind the rows ``after`` adds to ``author``; return their keys. Never
        raises: it runs AFTER the bytes landed, and a failure here must not
        report a saved file as failed — the caller would retry a write that
        succeeded, and the retry adds nothing to credit (review round 1). The
        rows then run as nobody, which is what they did before this existed."""
        try:
            return await self._credit(item_id, path, before=before, after=after, author=author)
        except Exception:  # noqa: BLE001 — logged; the save stands
            logger.exception(
                "schedule authorship: could not credit %s %s to %s", item_id, path, author
            )
            return []

    async def _credit(
        self, item_id: str, path: str, *, before: str | None, after: str, author: str
    ) -> list[str]:
        from .offered import unparsable_workflow

        if not author:
            return []
        # A file that was there but could not be read had rows nobody can name:
        # the fix that makes them readable did not ADD them (review round 1,
        # defect 1 — every row of a once-broken file went to whoever fixed it).
        if before is not None and _unreadable(before):
            return []
        if self._max_rows is not None and over_cap(after, self._max_rows) is not None:
            return []
        old = {key for key, _row in file_keys(item_id, path, before)} if before else set()
        added = [(key, row.run) for key, row in file_keys(item_id, path, after) if key not in old]
        if not added or not await asyncio.to_thread(self._may_run, author, item_id):
            return []
        credited: list[str] = []
        for key, run in added:
            if await asyncio.to_thread(self._bindings.get, key) is not None:
                continue
            if await unparsable_workflow(self._read, item_id, run) is not None:
                continue
            digest = await workflow_digest(self._read, item_id, run)
            await asyncio.to_thread(
                self._bindings.bind,
                key,
                item_id=item_id,
                path=path,
                user_id=author,
                workflow_digest=digest,
                reason="author",
            )
            credited.append(key)
        return credited
