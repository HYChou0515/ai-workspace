"""Which items have page-declared schedules (WUI third round).

The trigger sweep enumerates apps × profiles today: a small, static set. Reading
item-level declarations would change that to "every item", and items grow
without limit. This is the short list the sweep reads instead — the same shape
the platform already uses for per-item facts it must resolve without a session
(``_SandboxActivity``, ``_SandboxAddress``): one opaque row per item, registered
post-``spec.apply`` so its CRUD routes are never emitted.

**Maintained on the WRITE path, not in a route.** A page writes its
``schedules.json`` through the file PUT route; the agent writes the same file
through its ``write_file`` tool, which never touches a route. Hooking routes
would catch one and miss the other, so the hook sits at the chokepoint every
write already shares — where the quota gate sits, for the same reason.

**Stale in one direction only.** The index may name a file that has since been
deleted; it may never miss one that exists. So the sweep re-reads each path and
drops what it cannot find, and deletes need no hook of their own. That is one
fewer exit to cover, and exits are what get missed.
"""

from __future__ import annotations

import contextlib
import logging

from msgspec import Struct
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

logger = logging.getLogger(__name__)

#: The file a page writes its schedules into, inside its own folder.
SCHEDULES_FILE = "schedules.json"


class _ScheduleIndex(Struct):
    """``resource_id == item_id``, so a lookup is a point read and the sweep's
    listing is the whole (short) table."""

    paths: list[str] = []


def register_schedule_index(spec: SpecStar) -> None:
    """Idempotently register the model, post-``spec.apply`` so its auto-CRUD
    routes stay unemitted — this is platform bookkeeping, not something any
    authenticated caller may PUT."""
    with contextlib.suppress(ValueError):
        spec.add_model(_ScheduleIndex)


def is_schedule_file(path: str) -> bool:
    """Does this write mean "an item now has schedules"?

    Deliberately exact, and checked on every write in the platform. A loose
    match (``endswith``) would index items for ``schedules.json.bak`` and make
    the sweep read them forever.

    A file at the workspace ROOT does not count: a schedule belongs to a page,
    a page is a folder, and a view file at the root has no folder of its own.
    """
    parts = path.strip("/").split("/")
    return len(parts) == 2 and parts[1] == SCHEDULES_FILE


class ScheduleIndex:
    """The short list of items whose pages declare schedules."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _row(self, item_id: str) -> _ScheduleIndex | None:
        rm = self._spec.get_resource_manager(_ScheduleIndex)
        try:
            data = rm.get(item_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        return data if isinstance(data, _ScheduleIndex) else None

    def record(self, item_id: str, path: str) -> bool:
        """Note that this item has a schedule file at ``path``. Returns whether
        it actually wrote.

        The return value is not decoration: a page saves on every edit, and this
        runs on the write path, so "already known" has to cost a point read and
        NOT a round-trip. Without something observable, that property is
        untestable — a mutation deleting the guard changed no assertion, because
        the set below deduplicates either way and the only difference was an
        invisible extra write.
        """
        rm = self._spec.get_resource_manager(_ScheduleIndex)
        row = self._row(item_id)
        if row is None:
            with contextlib.suppress(Exception):
                rm.create(_ScheduleIndex(paths=[path]), resource_id=item_id)
                return True
            row = self._row(item_id)  # lost a race; fall through and merge
        if row is None or path in row.paths:
            return False
        rm.update(item_id, _ScheduleIndex(paths=sorted({*row.paths, path})))
        return True

    def forget(self, item_id: str, path: str) -> None:
        """Drop a path the sweep could not read. Quiet when the row is already
        gone: two pods may sweep the same item at once.

        The row itself goes when its last path does — an item whose schedules
        are deleted must stop being read, or the sweep pays for it on every pass
        forever.
        """
        rm = self._spec.get_resource_manager(_ScheduleIndex)
        row = self._row(item_id)
        if row is None or path not in row.paths:
            return
        rest = [p for p in row.paths if p != path]
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            if rest:
                rm.update(item_id, _ScheduleIndex(paths=rest))
            else:
                rm.delete(item_id)

    def items(self) -> list[str]:
        """Every item with at least one schedule file. The sweep's whole input."""
        rm = self._spec.get_resource_manager(_ScheduleIndex)
        # `is_deleted() == False` because `list_resources` happily returns
        # soft-deleted rows. Without it an item whose last schedule was removed
        # keeps being read on every sweep, forever — which is the one cost this
        # index exists to avoid.
        query = (QB.is_deleted() == False).build()  # noqa: E712
        out: list[str] = []
        for res in rm.list_resources(query, returns=["info"]):
            out.append(res.info.resource_id)  # ty: ignore[unresolved-attribute]
        return sorted(out)

    def paths(self, item_id: str) -> list[str]:
        row = self._row(item_id)
        return list(row.paths) if row else []
