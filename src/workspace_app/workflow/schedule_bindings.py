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

import contextlib
from collections.abc import Awaitable, Callable

from msgspec import Struct
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from .workspace_store import load_workspace_workflow_digested


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
