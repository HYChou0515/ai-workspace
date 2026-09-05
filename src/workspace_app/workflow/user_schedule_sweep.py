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

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from specstar import SpecStar

from ..api.schedule_index import ScheduleIndex
from ..filestore.protocol import FileNotFound
from .triggers import SpecstarTriggerStore, fire_window, is_due
from .user_schedules import trigger_id_for, usable_rows

logger = logging.getLogger(__name__)

#: Most schedules one page may declare — a RUNAWAY GUARD, not a policy limit.
#:
#: Deliberately far above any real use. A page's schedules are written by a
#: person choosing when they want things; a thousand of them means the page has
#: a bug, and the honest response to a bug is to be loud rather than to quietly
#: do the first N. Overridable per deploy (`server.max_page_schedules`), because
#: a number in the source is a number nobody can change when they need to.
#:
#: What it bounds is durable state: every schedule that fires leaves a row in the
#: window ledger, and nothing else caps how many a page can create.
DEFAULT_MAX_ROWS = 1000

#: How many times running a due schedule may fail before its window is left spent.
#:
#: The claim is taken before the run is asked for, so a failed start has to hand
#: the window back or the schedule silently misses that period. Handing it back
#: without a limit is the other failure: an item with no owner, or a workflow
#: somebody deleted, becomes one attempt a minute for as long as the period
#: lasts. A few tries absorbs a blip; after that the window is spent and the log
#: says so once instead of a thousand times.
#:
#: Counted in memory, per pod: it is a property of "this run of the sweep",
#: resets on restart, and needs no durable row of its own.
MAX_START_ATTEMPTS = 3

ReadFile = Callable[[str, str], Awaitable[bytes]]
OwnerOf = Callable[[str], str]


def _utc_now() -> datetime:
    """Now, in UTC, naive — the period math here is naive-local, so each row is
    converted into ITS zone before any of it runs.

    Deliberately not `datetime.now`: a server-local clock makes a schedule fire
    at a different moment depending on which pod ran the sweep, and a page that
    named no zone would silently mean "wherever this happens to be deployed".
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _in_zone(now_utc: datetime, tz: str) -> datetime:
    """`now` as the wall clock in `tz`, naive. An empty zone means UTC, which is
    the same rule the engineer-authored triggers use (`TriggerSweeper._local_now`)
    so the two engines cannot disagree about what "09:00" means.

    An unknown zone falls back to UTC rather than raising: the row already
    passed validation, and taking down one page's whole file — or the sweep —
    over a typo in a zone name is a worse answer than firing an hour out.
    """
    if not tz:
        return now_utc
    try:
        return now_utc.replace(tzinfo=UTC).astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        logger.warning("user schedules: unknown time zone %r — using UTC", tz)
        return now_utc


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
        now: Callable[[], datetime] = _utc_now,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        self._index = index
        self._read = read
        self._start = start
        self._owner_of = owner_of
        self._now = now
        self._max_rows = max_rows
        self._store = SpecstarTriggerStore(spec)
        self._failures: dict[str, int] = {}

    async def tick(self) -> int:
        """Fire everything due. Returns how many runs were launched."""
        fired = 0
        # Every store call below is BLOCKING specstar I/O — on Postgres, a network
        # round trip each. The sweep this one is modelled on offloads all of them,
        # and `SpecstarTriggerStore`'s docstring states the contract: the store is
        # sync, the sweeper is what puts it on a thread. This loop runs on every
        # API pod, un-gated by `run_consumers`, at O(items × paths × rows) per
        # tick, so a loop it holds is holding every request that pod is serving.
        for item_id in await asyncio.to_thread(self._index.items):
            for path in await asyncio.to_thread(self._index.paths, item_id):
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
        except (FileNotFound, FileNotFoundError):
            # GONE, specifically. Drop it: the index is allowed to be stale only
            # in this direction, and correcting it here is what lets a delete
            # need no hook of its own.
            logger.info("user schedules: %s %s is gone — dropping from the index", item_id, path)
            self._index.forget(item_id, path)
            return 0
        except Exception:
            # "Could not read it just now" is a DIFFERENT answer, and it must not
            # unregister anything. `files.read` raises for reasons that are not
            # deletion — `SandboxBusy`, which the facade propagates on purpose; a
            # 502 or timeout from the sandbox host; a sandbox mid-restore. And
            # `forget` is destructive: the last path takes the whole index row
            # with it, and only a WRITE of `schedules.json` ever re-creates it.
            # Reading a blip as a deletion stops a daily report forever and
            # leaves one log line saying the file is gone.
            logger.exception(
                "user schedules: %s %s could not be read this pass — leaving it indexed",
                item_id,
                path,
            )
            return 0

        rows, problems = usable_rows(raw)
        if len(rows) + len(problems) > self._max_rows:
            # The WHOLE file, unlike a single invalid row. A file with a thousand
            # entries was not typed by a person, so there is no good half worth
            # preserving — and half-processing would leave a durable ledger row
            # for every one it got through, which is the thing this bounds.
            logger.error(
                "user schedules: %s %s declares %d schedules, over the limit of %d — "
                "none will run until it is reduced",
                item_id,
                path,
                len(rows) + len(problems),
                self._max_rows,
            )
            return 0
        if problems:
            # Named, not raised, and PER ROW: a typo in one schedule must not
            # stop the others in the same file. Whole-file rejection is how
            # somebody's working report stops arriving because a colleague
            # mistyped a different one.
            logger.warning("user schedules: %s %s: %s", item_id, path, "; ".join(problems[:3]))

        folder = path.rsplit("/", 1)[0]
        owner = await asyncio.to_thread(self._owner_of, item_id)
        now_utc = self._now()
        fired = 0
        for row in rows:
            trigger_id = trigger_id_for(item_id, folder, row)
            schedule = row.as_schedule()
            # PER ROW, in the zone that row named. `tz` used to be accepted,
            # copied onto the Schedule and hashed into the lease key, and then
            # never consulted — the sweep asked the server what time it was. A
            # page saying "09:00, Asia/Taipei" on a UTC pod fired at 17:00 Taipei
            # time, every day, with nothing to notice: the report still arrived.
            now = _in_zone(now_utc, row.tz)
            last = await asyncio.to_thread(self._store.last_window, trigger_id)
            if not is_due(schedule, now, last):
                continue
            window = fire_window(schedule, now)
            # CLAIM BEFORE FIRING. Two pods sweep the same item at the same
            # second; the CAS lets exactly one of them through, and the loser
            # does nothing rather than sending a second copy of the mail.
            if not await asyncio.to_thread(self._store.try_claim, trigger_id, window):
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
                # Hand the window BACK, up to a point. The claim is taken before
                # the run is asked for — that ordering is what makes two pods
                # produce one run — so a failed start otherwise leaves the ledger
                # saying this window fired for a run that does not exist, and
                # nothing ever retries it. Catch-up covers a sweeper that was
                # DOWN at nine; it cannot see a window that was claimed and
                # dropped, so the report simply misses that day.
                #
                # BOUNDED, because releasing unconditionally turns a permanent
                # failure — an item with no owner, a workflow that was deleted —
                # into one attempt a minute forever. After the cap the window is
                # left spent and the log says so once, loudly, rather than a
                # thousand times quietly.
                tries = self._failures.get(trigger_id, 0) + 1
                self._failures[trigger_id] = tries
                if tries < MAX_START_ATTEMPTS:
                    await asyncio.to_thread(self._store.release_claim, trigger_id, window, last)
                    logger.exception(
                        "user schedules: %s could not start %s for window %s "
                        "(attempt %d of %d) — window released to try again",
                        trigger_id,
                        row.run,
                        window,
                        tries,
                        MAX_START_ATTEMPTS,
                    )
                else:
                    logger.error(
                        "user schedules: %s could not start %s %d times running — "
                        "giving up on window %s. Nothing will run for it.",
                        trigger_id,
                        row.run,
                        tries,
                        window,
                    )
                continue
            # A run started, so whatever was wrong is over.
            self._failures.pop(trigger_id, None)
            fired += 1
        return fired
