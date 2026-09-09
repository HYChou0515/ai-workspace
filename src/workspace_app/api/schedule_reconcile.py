"""Bring the schedule index back in step with what an item actually holds.

The index is fed by hooks at the write boundaries — the facade's `_landed`, and
since P40 the sandbox mirror's `on_write`. Hooks are the cheap path and they
stay. This is the backstop, because a hook per door is a LIST OF DOORS, and every
hand-written list in this feature has gone stale in silence:

* `registry._writeback` returns before `sync.mirror` on a host-managed durable
  deployment, so on that branch the mirror hook never fires at all and P40's fix
  — a `schedules.json` written by an agent's `exec` — does nothing. The codebase
  already documents this for the sibling hook on the same constructor
  (`_reconcile_after_turn` compensates for `on_measured` for exactly this
  reason); `on_write` had no compensation.
* `apps.seeding.seed_item` and the `/collections.json` route write straight to
  the raw filestore, past the facade AND the mirror. Nothing ships a `view: wui`
  folder through them today, which is the only reason it does not bite.

So: at the turn boundary, ask what the item holds and record what counts. One
listing — the same call the file tree makes — and `record` is a CAS merge, so
running it beside the hooks costs a row read and changes nothing when they
worked.

ADDITIVE on purpose. Replacing the row would race the mirror: whichever finished
last would win, and a page saved during a turn could be dropped by a reconcile
that listed the workspace a moment earlier. Removal stays where it already is —
the sweep reads a path, gets `FileNotFound`, CONFIRMS that against the live
store, and forgets it then. A listing cannot tell "deleted" from "the mirror has
not caught up", and guessing wrong is how a daily report stops without anyone
being told.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .schedule_index import ScheduleIndex, is_schedule_file

logger = logging.getLogger(__name__)

ListFiles = Callable[[str], Awaitable[list[str]]]


async def reconcile_item_schedules(item_id: str, *, ls: ListFiles, index: ScheduleIndex) -> None:
    """Record every schedule declaration ``item_id`` holds. Never raises.

    Runs after the user's work is done and persisted, so a failure here means the
    index is a little behind — which the next write, or the next turn, corrects.
    Raising would turn that into a turn reporting an error, which costs more than
    the lag.
    """
    try:
        paths = await ls(item_id)
    except Exception:
        logger.exception("schedule reconcile: could not list %s", item_id)
        return

    declared = [p for p in paths if is_schedule_file(p)]
    if not declared:
        # Nothing to reconcile, and the overwhelmingly common case. Not even a
        # read: a workspace with no page must not pay for this at all.
        return

    # ASK ONCE what is already known, then write only what is new.
    #
    # Steady state is a turn end that finds exactly what the hooks already
    # recorded, and `record` costs TWO round trips for a path it already holds
    # (`create(if_not_exists=True)` refuses, then a read). Recording each path
    # unconditionally therefore made a workspace with three pages pay six
    # round trips per turn to learn nothing.
    try:
        known = set(await asyncio.to_thread(index.paths, item_id))
    except Exception:
        logger.exception("schedule reconcile: could not read the index for %s", item_id)
        return

    for path in declared:
        if path in known:
            continue
        try:
            # OFFLOADED. `record` is blocking specstar I/O and this runs at every
            # turn end, on the API's event loop. `_note_schedule_file` keeps the
            # synchronous call and argues for it — "the cost is paid only by an
            # actual CHANGE to a `schedules.json` ... If that stops being true
            # the answer is an async hook" — and this reconciler is what made it
            # stop being true. So this is that async hook; the facade's own
            # write tail stays synchronous because it really is per-change.
            await asyncio.to_thread(index.record, item_id, path)
        except Exception:
            # Per PATH, so one bad row does not cost the others theirs — the same
            # rule the sweep itself keeps.
            logger.exception("schedule reconcile: could not record %s %s", item_id, path)
