"""Who a page schedule runs AS (`docs/plan-wui-viewer-login.md` Q7/Q8).

A schedule has no request behind it, so by default its tools get the shared
layer and the deploy's request-less answer only — nobody's private values. A
person may lend theirs by pressing "run as me": that writes a binding here, and
from then on the schedule's fires carry that person's private layer.

Three rules, each for a reason:

* **The binding is to the schedule's KEY, which is derived from its content**
  (`user_schedules.trigger_id_for`). Edit the row and it is a different
  schedule with no binding — so "bind the harmless one, then swap in the
  payroll query" cannot run the payroll query as the person who consented. The
  sweep notices a binding whose key its file no longer holds, drops it and says
  so to the binder.
* **One person per schedule** (Q8). Another may take it over; the one replaced
  is told. Per-person fan-out would multiply every fire by its subscribers.
* **Only the person themself can create a binding in their name** — the route
  binds the CALLER. Whoever writes `schedules.json` (any `edit_content` holder,
  the page, the agent) chooses nothing about whose values it runs with.

Registered post-``spec.apply``: no auto-CRUD, so the routes are the only door.
"""

from __future__ import annotations

import contextlib

from msgspec import Struct
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..api.timeutil import now_ms


class ScheduleBinding(Struct):
    trigger_id: str
    item_id: str
    #: The schedules file the row lives in — the sweep compares a binding only
    #: against the file it came from.
    path: str
    user_id: str
    bound_at: int


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

    def bind(self, trigger_id: str, *, item_id: str, path: str, user_id: str) -> str:
        """Bind the schedule to ``user_id``, replacing whoever held it. Returns
        the replaced person ("" when there was none, or it was them already)."""
        row = ScheduleBinding(
            trigger_id=trigger_id, item_id=item_id, path=path, user_id=user_id, bound_at=now_ms()
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
