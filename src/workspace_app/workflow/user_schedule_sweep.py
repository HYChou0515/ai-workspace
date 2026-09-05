"""Fire the schedules a page declared (WUI third round).

Everything this needs already existed. ``ScheduleIndex`` says which items to
read — a short list, never every item. ``SpecstarTriggerStore`` holds the window
ledger and its CAS claim, so two pods produce one run and a missed window fires
late instead of being dropped. ``is_due`` decides the moment. This module is the
join, and its whole job is to be boring.

Two properties matter more than anything it does:

**One page's mistake costs that page only.** The sweep reads every item that has
schedules, so a single unreadable file must not take everyone else's down with
it. A page is written by an LLM; broken files are the normal case, not the edge.
That is why parsing lints instead of raising, and why every step here is
per-item resilient.

**The index is corrected from here.** It may name a file that has since been
deleted — deletes have no hook of their own, deliberately, because a hook per
exit is a hook that gets missed. So a path that cannot be read is dropped, and
an item whose last path goes stops being read at all.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from specstar import SpecStar

from ..api.schedule_index import ScheduleIndex
from .triggers import SpecstarTriggerStore, fire_window, is_due
from .user_schedules import trigger_id_for, usable_rows

logger = logging.getLogger(__name__)

ReadFile = Callable[[str, str], Awaitable[bytes]]
OwnerOf = Callable[[str], str]


class StartRun(Protocol):
    """Launch one run. Kept narrow on purpose: the sweep decides WHEN, and
    nothing about how a workflow runs."""

    async def __call__(
        self, *, item_id: str, workflow_id: str, acting_user: str, payload: dict[str, Any]
    ) -> str | None: ...


class UserScheduleSweeper:
    """One pass over every item that has page-declared schedules."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        index: ScheduleIndex,
        read: ReadFile,
        start: StartRun,
        owner_of: OwnerOf,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._index = index
        self._read = read
        self._start = start
        self._owner_of = owner_of
        self._now = now
        self._store = SpecstarTriggerStore(spec)

    async def tick(self) -> int:
        """Fire everything due. Returns how many runs were launched."""
        fired = 0
        for item_id in self._index.items():
            for path in self._index.paths(item_id):
                try:
                    fired += await self._one_file(item_id, path)
                except Exception:
                    # Per-item resilience, the rule every sweep here keeps: one
                    # item's problem must not cost the rest their schedules. The
                    # failure that would otherwise be found weeks later, by
                    # somebody asking why their report stopped.
                    logger.exception("user schedules: item %s path %s failed", item_id, path)
        return fired

    async def _one_file(self, item_id: str, path: str) -> int:
        try:
            raw = (await self._read(item_id, path)).decode("utf-8", "replace")
        except Exception:
            # The file is gone, or unreadable. Drop it: the index is allowed to
            # be stale only in this direction, and correcting it here is what
            # lets a delete need no hook of its own.
            logger.info("user schedules: %s %s is gone — dropping from the index", item_id, path)
            self._index.forget(item_id, path)
            return 0

        rows, problems = usable_rows(raw)
        if problems:
            # Named, not raised, and PER ROW: a typo in one schedule must not
            # stop the others in the same file. Whole-file rejection is how
            # somebody's working report stops arriving because a colleague
            # mistyped a different one.
            logger.warning("user schedules: %s %s: %s", item_id, path, "; ".join(problems[:3]))

        folder = path.rsplit("/", 1)[0]
        owner = self._owner_of(item_id)
        now = self._now()
        fired = 0
        for row in rows:
            trigger_id = trigger_id_for(item_id, folder, row)
            schedule = row.as_schedule()
            if not is_due(schedule, now, self._store.last_window(trigger_id)):
                continue
            window = fire_window(schedule, now)
            # CLAIM BEFORE FIRING. Two pods sweep the same item at the same
            # second; the CAS lets exactly one of them through, and the loser
            # does nothing rather than sending a second copy of the mail.
            if not self._store.try_claim(trigger_id, window):
                continue
            try:
                await self._start(
                    item_id=item_id,
                    workflow_id=row.run,
                    # The item's owner, not whoever last edited the file: a
                    # scheduled run has no request, so there is no personal
                    # credential to inherit, and the item is already the
                    # boundary everything else here is scoped to.
                    acting_user=owner,
                    payload=row.payload,
                )
            except Exception:
                logger.exception(
                    "user schedules: %s could not start %s for window %s",
                    trigger_id,
                    row.run,
                    window,
                )
                continue
            fired += 1
        return fired
