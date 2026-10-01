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
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from specstar import SpecStar

from ..api.schedule_index import ScheduleIndex
from ..filestore.protocol import FileNotFound
from .offered import no_such_workflow, unparsable_workflow
from .orchestrator import ActiveRunExists
from .schedule_bindings import ScheduleBinding, ScheduleBindings, workflow_digest
from .triggers import ScanLease, SpecstarTriggerStore, fire_window, is_due
from .user_schedules import (
    file_rows,
    in_zone,
    over_cap,
    trigger_id_for,
    usable_rows,
    utc_now,
)

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
#: Counted per (trigger, WINDOW) — "how many tries this window gets", not "how
#: many times this schedule may ever fail". Counted per trigger instead, a report
#: that had a bad day in January would be abandoned on its first stumble in
#: February and every month after, with the log still saying "3 times running":
#: the blip absorption gone for good on exactly the schedules that had already
#: had trouble.
#:
#: In memory, per pod. It is a property of "this run of the sweep", resets on
#: restart, and needs no durable row of its own.
MAX_START_ATTEMPTS = 3

#: "I could not get an answer", as distinct from "it is not there".
#:
#: Collapsing those two is how a read failure became a deletion: the snapshot
#: says missing (ordinary, while the mirror catches up), the live read then
#: raises `SandboxBusy` or times out, and a two-valued answer files that as
#: gone — permanently, since only a WRITE of `schedules.json` re-creates the
#: row. The sweep already refuses to make that inference on its FIRST read; this
#: is what lets the confirming read refuse it too.
UNKNOWN = object()

#: How long the confirming read may take before it counts as "could not say".
#:
#: That read reaches the LIVE workspace, and on the hosted backend a cold one is
#: a full restore. `tick` is sequential over items, so an unbounded wait behind
#: ONE deleted schedule delays every other item's schedules on that pod — against
#: a sweeper whose whole promise is that the interval is the latest a run will be.
#:
#: Timing out is not evidence of absence. It is :data:`UNKNOWN`: the path stays
#: indexed and the next tick asks again, which costs one tick rather than the
#: schedule.
DEFAULT_CONFIRM_TIMEOUT_S = 10.0

ReadFile = Callable[[str, str], Awaitable[bytes]]
OwnerOf = Callable[[str], str]
#: Which workflows this app offers the given item, or None for "unrestricted".
#: Unset means the deploy wired no resolver and behaves as it did before this
#: existed — the same rule the tool ceiling keeps, never "refuse everything".
#: Awaited: the item's own `.workflows/` are files, and one shared resolver
#: (`workflow.offered`) answers this for every entrance.
WorkflowsFor = Callable[[str], Awaitable[Sequence[str] | None]]


class StartRun(Protocol):
    """Launch one run. Kept narrow on purpose: the sweep decides WHEN, and
    nothing about how a workflow runs.

    **Raising means NOTHING STARTED.** The sweep claims the window before asking,
    so a raise is its signal to hand that window back and try again — which it
    cannot distinguish from "the run began and the bookkeeping after it failed".
    An implementation that raises AFTER its side effect has happened therefore
    causes the same window to fire again, with no shared chat id to collide on,
    and the person gets two of whatever this sends.

    So: once the run exists, swallow and log. Anything else is a promise this
    caller cannot keep.
    """

    async def __call__(
        self,
        *,
        item_id: str,
        workflow_id: str,
        acting_user: str,
        payload: dict[str, Any],
        key: str,
        env_user: str,
        env_digest: str | None = None,
    ) -> str | None: ...


class UserScheduleSweeper:
    """One pass over every item that has page-declared schedules."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        index: ScheduleIndex,
        read: ReadFile,
        read_live: ReadFile | None = None,
        start: StartRun,
        owner_of: OwnerOf,
        workflows_for: WorkflowsFor | None = None,
        now: Callable[[], datetime] = utc_now,
        max_rows: int = DEFAULT_MAX_ROWS,
        confirm_timeout_s: float = DEFAULT_CONFIRM_TIMEOUT_S,
        lease: ScanLease | None = None,
        bindings: ScheduleBindings | None = None,
        on_expired: Callable[[ScheduleBinding, str], object] | None = None,
        binder_may: Callable[[str, str], bool] | None = None,
    ) -> None:
        #: Whether a binder may STILL make this item run work (``execute``),
        #: asked at every fire: a person removed from the item must stop lending
        #: their values the moment they are removed (review round 1, C2/F1).
        #: None ⇒ unchecked (a composition that wired none).
        self._binder_may = binder_may
        #: `plan-wui-viewer-login` Q7: who each schedule runs AS. None ⇒ no
        #: store wired, every fire runs as nobody in particular (as before).
        self._bindings = bindings
        #: Told about each binding a file edit invalidated, so its binder learns
        #: their name is no longer on it. Best effort: a failure here must not
        #: cost the tick.
        self._on_expired = on_expired
        self._index = index
        #: #804: None ⇒ every caller scans (a single process, or a test that is
        #: not about pods). The API passes one so that N pods cost one scan.
        self._lease = lease
        self._read = read
        #: Consulted ONLY to confirm a deletion. `read` is the durable snapshot,
        #: which is what keeps the ordinary tick from waking a reaped sandbox —
        #: but the snapshot LAGS the workspace, so "not there yet" and "deleted"
        #: arrive as the same answer, and unregistering is not undoable. This is
        #: the live workspace, asked once, on the one path where being wrong
        #: costs a schedule.
        self._read_live = read_live
        self._start = start
        self._owner_of = owner_of
        self._workflows_for = workflows_for
        self._now = now
        self._max_rows = max_rows
        self._confirm_timeout_s = confirm_timeout_s
        self._store = SpecstarTriggerStore(spec)
        self._failures: dict[tuple[str, str], int] = {}
        #: The last complaint said about each file, so an unchanged one is not
        #: repeated. A file with a typo is re-read every tick, and this module
        #: already argues the point about its own retry cap: "the log says so
        #: once instead of a thousand times", because a channel trained to be
        #: noise is one where the line that mattered is not read either.
        #: In memory, per pod — it is a property of this run of the sweep.
        self._said: dict[tuple[str, str], str] = {}

    async def tick(self) -> int:
        """Fire everything due. Returns how many runs were launched."""
        fired = 0
        if self._lease is not None and not await asyncio.to_thread(self._lease.claim):
            return fired  # another pod is scanning this window
        # Every store call in this sweep — here and in `_one_file` — is BLOCKING
        # specstar I/O; on Postgres, a network round trip each. The sweep this
        # one is modelled on offloads all of them,
        # and `SpecstarTriggerStore`'s docstring states the contract: the store is
        # sync, the sweeper is what puts it on a thread. This loop runs on every
        # API pod, un-gated by `run_consumers`, at O(items × paths × rows) per
        # tick, so a loop it holds is holding every request that pod is serving.
        for item_id, paths in await asyncio.to_thread(self._index.items_with_paths):
            # PER PATH, because a page is a folder and the promise in this
            # module's header is "one page's mistake costs that page only".
            #
            # This handler has now been wrong in both directions. It started
            # outside the paths READ, so one specstar error skipped every item
            # after this one; moving it in fixed that and widened it to the whole
            # item at the same time, which made the alphabetically-first page
            # cost every page after it — the same argument, one level down.
            # `paths` arrives with the listing now, so nothing has to be read
            # here and the handler can sit where the blast radius belongs.
            for path in paths:
                try:
                    fired += await self._one_file(item_id, path)
                except Exception:
                    # The failure that would otherwise be found weeks later, by
                    # somebody asking why their report stopped.
                    logger.exception("user schedules: item %s path %s failed", item_id, path)
        return fired

    def _say_once(self, item_id: str, path: str, level: int, message: str, *args: object) -> None:
        """Log this file's complaint, unless it is the one already said about it.

        Repeats when the complaint CHANGES, because that is new information —
        the author edited the file and it is wrong in a different way.
        """
        rendered = message % args if args else message
        if self._said.get((item_id, path)) == rendered:
            return
        self._said[item_id, path] = rendered
        logger.log(level, "%s", rendered)

    async def _still_there(self, item_id: str, path: str) -> bytes | None | object:
        """Three answers, not two: the bytes, `None` for confirmed gone, or
        :data:`UNKNOWN` when the question could not be answered.

        The distinction is the whole point. A read that FAILS is not evidence of
        absence — `files.read` raises `SandboxBusy` (which the facade propagates
        deliberately), 502s from the sandbox host, and everything a half-restored
        workspace throws. Answering `None` to those made the caller unregister a
        schedule that exists, which is the failure this confirmation was added to
        prevent, arriving through the confirmation itself.

        The reasoning that produced the bug is worth keeping visible: "the caller
        was already about to drop this path, so the worst case is one lost
        schedule". It is wrong because the caller was about to drop it ONLY on the
        snapshot's word — and doubting exactly that word is why this function
        exists.
        """
        if self._read_live is None:
            # No live reader wired. The snapshot is all there is, so its answer
            # stands: this is a deploy that opted out of confirming, not one that
            # tried and failed.
            return None
        try:
            return await asyncio.wait_for(self._read_live(item_id, path), self._confirm_timeout_s)
        except (FileNotFound, FileNotFoundError):
            return None
        except TimeoutError:
            # Bounded, because this read can be a whole sandbox restore and the
            # tick is sequential: one slow answer would delay every other item's
            # schedules on this pod. A timeout says nothing about whether the
            # file is there, so it is UNKNOWN and the path stays indexed.
            logger.warning(
                "user schedules: confirming %s %s took longer than %.0fs — leaving it indexed",
                item_id,
                path,
                self._confirm_timeout_s,
            )
            return UNKNOWN
        except Exception:
            logger.exception(
                "user schedules: could not confirm whether %s %s still exists — leaving it indexed",
                item_id,
                path,
            )
            return UNKNOWN

    async def _binder_for(
        self, item_id: str, trigger_id: str, workflow_id: str
    ) -> tuple[str, str | None]:
        """Whose private values this fire runs with: the binder — if they may
        still make this item run work, and the workflow is still the one they
        consented to. Otherwise the binding is dropped and they are told why;
        the fire runs as nobody. Returns ``(binder, the workflow digest they
        consented to)`` — the run records that digest, not one read at start.

        A check that FAILS, at any step, runs the fire as nobody too — never as
        a binder it could not check, and never not at all (a raise here would
        reach the start's handler and skip the window). Round 2 R4 guarded only
        the read; round 3 (defect 2) found the access check and the unbind
        outside it."""
        if self._bindings is None:
            return "", None
        try:
            return await self._checked_binder(self._bindings, item_id, trigger_id, workflow_id)
        except Exception:  # noqa: BLE001 — see the docstring
            logger.exception("user schedules: %s: could not check the binding", trigger_id)
            return "", None

    async def _checked_binder(
        self, bindings: ScheduleBindings, item_id: str, trigger_id: str, workflow_id: str
    ) -> tuple[str, str | None]:
        binding = await asyncio.to_thread(bindings.get, trigger_id)
        if binding is None:
            return "", None
        if self._binder_may is not None and not await asyncio.to_thread(
            self._binder_may, binding.user_id, item_id
        ):
            await asyncio.to_thread(bindings.unbind, trigger_id)
            await self._tell(binding, "no_access")
            return "", None
        if await self._workflow_changed(item_id, workflow_id, binding.workflow_digest):
            await asyncio.to_thread(bindings.unbind, trigger_id)
            await self._tell(binding, "changed")
            return "", None
        return binding.user_id, binding.workflow_digest

    async def _workflow_changed(self, item_id: str, workflow_id: str, bound: str) -> bool:
        """Has the workflow's body changed since the binder consented (F4)? Asked
        of the LIVE file — the one the run will load, and the one the binding
        was made on (round 2, D3: the snapshot matching said nothing about
        it). Only read when the schedule is bound. A read that fails, or does
        not answer within the sweep's confirmation bound, decides nothing (keep
        the binding): a live read can rebuild a reaped sandbox, and the sweep
        walks items one after another, so an unbounded one held up every other
        item's schedules (round 3, regression 1)."""
        try:
            live = self._read_live or self._read
            return (
                await asyncio.wait_for(
                    workflow_digest(live, item_id, workflow_id), self._confirm_timeout_s
                )
                != bound
            )
        except Exception:  # noqa: BLE001 — an unanswered question is not a change
            logger.warning(
                "user schedules: %s: could not read workflow %r to check a binding",
                item_id,
                workflow_id,
                exc_info=True,
            )
            return False

    async def _tell(self, binding: ScheduleBinding, why: str) -> None:
        """Tell the binder their name is off a schedule, and WHY — ``changed``
        (the row or file no longer holds it) or ``no_access`` (they may no
        longer run work in the item). The notice must say the true reason."""
        if self._on_expired is None:
            return
        try:
            await asyncio.to_thread(self._on_expired, binding, why)
        except Exception:  # noqa: BLE001 — a failed notice is not a failed schedule
            logger.exception("user schedules: could not tell %s", binding.user_id)

    async def _expire_bindings(self, item_id: str, path: str, raw: str) -> None:
        """Drop the bindings of rows this file no longer holds — an edited row is
        a new key — and tell each binder. Dropping is not undoable (the person
        has to press "Run as me" again), so a key must be absent from BOTH the
        snapshot and the live workspace (review round 1):

        * the snapshot LAGS the live file, and "Run as me" is pressed on what
          the page just saved — absent there only means "not synced yet" (F2);
        * a file that does not parse says nothing about which rows it holds — a
          stray brace must not cost every binder their consent (F3);
        * a live read that fails or times out decides nothing.

        A store failure is logged and the tick goes on."""
        assert self._bindings is not None
        snapshot = _keys_in(item_id, path, raw)
        if snapshot is None:
            return
        try:
            bound = await asyncio.to_thread(self._bindings.for_item, item_id)
        except Exception:  # noqa: BLE001 — one item's bookkeeping must not cost the tick
            logger.exception("user schedules: %s %s: could not check bindings", item_id, path)
            return
        if not any(b.path == path and b.trigger_id not in snapshot for b in bound):
            return  # the common case: nothing to confirm, no second read
        found = await self._still_there(item_id, path)
        if found is UNKNOWN:
            return
        if found is None:
            live: set[str] | None = set()
        else:
            assert isinstance(found, bytes)
            live = _keys_in(item_id, path, found.decode("utf-8", "replace"))
        if live is None:
            return
        await self._drop_bindings(item_id, path, keep=snapshot | live)

    async def _drop_bindings(self, item_id: str, path: str, *, keep: set[str]) -> None:
        assert self._bindings is not None
        try:
            gone = await asyncio.to_thread(self._bindings.expire_absent, item_id, path, keep)
        except Exception:  # noqa: BLE001 — one item's bookkeeping must not cost the tick
            logger.exception("user schedules: %s %s: could not check bindings", item_id, path)
            return
        for binding in gone:
            await self._tell(binding, "changed")

    async def _one_file(self, item_id: str, path: str) -> int:
        try:
            raw = (await self._read(item_id, path)).decode("utf-8", "replace")
        except (FileNotFound, FileNotFoundError):
            # MISSING FROM THE SNAPSHOT, which is not the same as gone. A page's
            # save lands in the warm sandbox and the snapshot catches up on the
            # next mirror, so a tick inside that window sees exactly this for a
            # file that is right there — and unregistering it stops the schedule
            # until somebody saves again, with a log line saying it was deleted.
            #
            # Two fixes that were each right alone made this reachable: narrowing
            # the catch to `FileNotFound` (so a blip is not read as a deletion)
            # and reading the snapshot (so the sweep stops resurrecting reaped
            # sandboxes) together made `FileNotFound` an ordinary transient state
            # for the first time.
            #
            # So ask the LIVE workspace before believing it — only here, so the
            # ordinary tick still reads the snapshot and wakes nothing.
            found = await self._still_there(item_id, path)
            if found is UNKNOWN:
                # Nobody could say. Leave it indexed and try again next tick —
                # the same answer the first read's own failure branch gives, for
                # the same reason: unregistering is not undoable.
                return 0
            if found is None:
                logger.info(
                    "user schedules: %s %s is gone — dropping from the index", item_id, path
                )
                await asyncio.to_thread(self._index.forget, item_id, path)
                # Confirmed gone (the live workspace agrees): its schedules are
                # gone, and so is every consent given to them (round 1, C12).
                if self._bindings is not None:
                    await self._drop_bindings(item_id, path, keep=set())
                return 0
            assert isinstance(found, bytes)
            raw = found.decode("utf-8", "replace")
        except Exception:
            # "Could not read it just now" is a DIFFERENT answer, and it must not
            # unregister anything. `files.read` raises for reasons that are not
            # deletion — `SandboxBusy`, which the facade propagates on purpose; a
            # 502 or timeout from the sandbox host; a sandbox mid-restore. And
            # `forget` is destructive: it empties the row, and only a WRITE of
            # `schedules.json` ever puts the path back.
            # Reading a blip as a deletion stops a daily report forever and
            # leaves one log line saying the file is gone.
            logger.exception(
                "user schedules: %s %s could not be read this pass — leaving it indexed",
                item_id,
                path,
            )
            return 0

        # BEFORE the parse, because the cap exists to bound exactly that work.
        # Counting after it meant a runaway file paid its full parse and was then
        # refused — every tick, on every pod, for as long as it stayed indexed.
        if (capped := over_cap(raw, self._max_rows)) is not None:
            # The WHOLE file, unlike a single invalid row. A file with a thousand
            # entries was not typed by a person, so there is no good half worth
            # preserving — and half-processing would leave a durable ledger row
            # for every one it got through, which is the thing this bounds.
            # The sentence is `over_cap`'s, shared with the panel's listing.
            self._say_once(
                item_id, path, logging.ERROR, "user schedules: %s %s %s", item_id, path, capped
            )
            return 0

        rows, problems = usable_rows(raw)
        if problems:
            # Named, not raised, and PER ROW: a typo in one schedule must not
            # stop the others in the same file. Whole-file rejection is how
            # somebody's working report stops arriving because a colleague
            # mistyped a different one.
            self._say_once(
                item_id,
                path,
                logging.WARNING,
                "user schedules: %s %s: %s",
                item_id,
                path,
                "; ".join(problems[:3]),
            )

        else:
            # Clean now — forget what was said, so a future problem is reported
            # rather than suppressed by a memo of a complaint that no longer
            # applies.
            self._said.pop((item_id, path), None)

        folder = path.rsplit("/", 1)[0]
        # HERE — after a clean read and parse, never on a read failure or an
        # over-cap file (both returned above) — because dropping a binding is
        # not undoable: the person has to press "run as me" again.
        if self._bindings is not None:
            await self._expire_bindings(item_id, path, raw)
        owner = await asyncio.to_thread(self._owner_of, item_id)
        # The ceiling `run` has to stay inside. Checked HERE and per ROW, the
        # same shape as every other lint in this file: the interactive entrance
        # refuses an unknown workflow with a sentence naming it, and the
        # scheduled one checked nothing — so a typo reached `orchestrator.start`,
        # failed an assertion deep inside, and surfaced as a generic "could not
        # start" in a log the page's author never reads. One mistyped id must
        # not stop the other schedules in the same file.
        offered: set[str] | None = None
        if self._workflows_for is not None:
            answer = await self._workflows_for(item_id)
            # `None` from the RESOLVER means unrestricted, exactly as an unwired
            # resolver does — `set(... or ())` collapsed it to the empty set,
            # which refuses every row. The outer check only ever covered "no
            # resolver"; a resolver that answers None is the documented value and
            # it did the opposite, silently, once per row per tick.
            offered = None if answer is None else set(answer)
        # Every `run` this file still complains about. Cleared below for the
        # ones it no longer does — the per-row memo's subject is a ROW, so "the
        # file has no lint problems" is the wrong moment to forget it: a row
        # naming a workflow the app does not offer is not a lint problem, so
        # that branch fired on every tick and cleared the memo it had just
        # written. The failure the memo exists to prevent — an author fixing a
        # bad `run:` and breaking it the same way next week to silence — needs
        # forgetting to happen when the ROW changes, not when the file parses.
        still_bad: set[str] = set()
        now_utc = self._now()
        fired = 0
        for row in rows:
            if offered is not None and row.run not in offered:
                # Memoised like the other two complaints about this file, and
                # keyed on the ROW as well as the path: this is the only one of
                # the three that multiplies by rows, so leaving it bare was a
                # line per bad row per tick, on every pod, until somebody edits a
                # page nothing has told them is broken. A lesson applied to two
                # of three places is worse than one not applied at all — the memo
                # makes the log look tamed while the line that floods it fires on.
                self._say_once(
                    item_id,
                    f"{path}#{row.run}",
                    logging.WARNING,
                    "user schedules: %s %s: %s That row will not run.",
                    item_id,
                    path,
                    no_such_workflow(row.run, offered),
                )
                still_bad.add(row.run)
                continue
            trigger_id = trigger_id_for(item_id, folder, row)
            schedule = row.as_schedule()
            # PER ROW, in the zone that row named. `tz` used to be accepted,
            # copied onto the Schedule and hashed into the lease key, and then
            # never consulted — the sweep asked the server what time it was. A
            # page saying "09:00, Asia/Taipei" on a UTC pod fired at 17:00 Taipei
            # time, every day, with nothing to notice: the report still arrived.
            now = in_zone(now_utc, row.tz)
            last = await asyncio.to_thread(self._store.last_window, trigger_id)
            if not is_due(schedule, now, last):
                continue
            # The item HAS the file — will it run? Asked only for a row that is
            # DUE (a tick with nothing due costs only the one read of the
            # schedules file — #804's test holds that) and BEFORE the claim, so
            # a window is never spent on a run that cannot start. A broken row
            # therefore stays due and is read once per tick until its file is
            # fixed — then it fires on the first tick after (catch-up); the
            # alternative, claiming the window for a run that cannot start,
            # would hold the fix back to the next period. Without this the row
            # reached `orchestrator.start`, failed an assertion deep inside and
            # handed its window back — three times, then the window was burned
            # with an ERROR — the reason only in a log nobody reads. Same memo
            # key as the "no such workflow" complaint: the subject is the ROW,
            # and a complaint that changes is said again.
            try:
                problem = await unparsable_workflow(self._read, item_id, row.run)
            except Exception:  # noqa: BLE001 — one row's read must not cost the tick
                # The schedules-file read three screens up survives a store
                # error per item; a per-row read must not do worse.
                logger.debug("user schedules: workflow read failed", exc_info=True)
                self._say_once(
                    item_id,
                    f"{path}#{row.run}#read",
                    logging.WARNING,
                    "user schedules: %s %s: could not read workflow %r — that row is skipped "
                    "this tick",
                    item_id,
                    path,
                    row.run,
                )
                continue
            if problem is not None:
                self._say_once(
                    item_id,
                    f"{path}#{row.run}",
                    logging.WARNING,
                    "user schedules: %s %s: workflow %r won't parse: %s That row will not run.",
                    item_id,
                    path,
                    row.run,
                    problem,
                )
                still_bad.add(row.run)
                continue
            window = fire_window(schedule, now)
            # CLAIM BEFORE FIRING. Two pods sweep the same item at the same
            # second; the CAS lets exactly one of them through, and the loser
            # does nothing rather than sending a second copy of the mail.
            if not await asyncio.to_thread(self._store.try_claim, trigger_id, window):
                continue
            try:
                binder, consented = await self._binder_for(item_id, trigger_id, row.run)
                await self._start(
                    item_id=item_id,
                    workflow_id=row.run,
                    # The item's owner, not whoever last edited the file: a
                    # scheduled run has no request, so nobody's cookie is on it
                    # to inherit, and the item is already the boundary
                    # everything else here is scoped to. The run's tools DO get
                    # the deploy's request-less answer (`env_without_request`)
                    # for this owner — an identity the file's editor did not
                    # need to hold. `api/app.py:_owner_of_item` says why that is
                    # the impl's policy to bound.
                    acting_user=owner,
                    payload=row.payload,
                    # The SAME id the window ledger claims on, so the chat this
                    # run drives and the lock that stops it running twice agree
                    # about what "this schedule" means.
                    key=trigger_id,
                    # Captured as the owner above; RUN WITH the binder's private
                    # values, or nobody's ("") when no one pressed "run as me".
                    env_user=binder,
                    env_digest=consented,
                )
            except ActiveRunExists:
                # NOT a failure. The schedule's previous fire is still running,
                # and colliding with it is what the stable per-schedule chat was
                # FOR — the one-run rule finally applies to the entrance that
                # repeats. Treating it as a failed start meant a traceback per
                # tick and, after three, the window burned with "Nothing will
                # run for it": `every: minutes, n: 1` against a two-minute
                # workflow is thousands of ERROR lines a day for a condition the
                # design intends.
                #
                # The window stays CLAIMED, deliberately. Handing it back would
                # only make the next tick collide again; skipping it is what an
                # overrun means, and the next window is the right place to try.
                # Said once per (schedule, window) so a slow run does not narrate
                # itself, and at INFO because nothing is wrong.
                # Keyed on the SCHEDULE, not the window. A slow run produces a
                # new window every period, so a window-keyed memo says the line
                # once per window — 1440 a day for a minutely schedule, which is
                # the flood it was added to stop — and keeps one dict entry per
                # window for the life of the process. `_failures` below prunes
                # exactly this way and says so; the memo beside it did not.
                #
                # A run spanning forty windows is ONE fact, so the message names
                # the schedule rather than whichever window we noticed in.
                self._say_once(
                    item_id,
                    f"{path}#busy",
                    logging.INFO,
                    "user schedules: %s is still running its previous fire — "
                    "skipping windows until it finishes",
                    trigger_id,
                )
                continue
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
                # Keyed by WINDOW, and the trigger's older windows are dropped
                # so this cannot grow with time.
                self._failures = {
                    k: v for k, v in self._failures.items() if k[0] != trigger_id or k[1] == window
                }
                tries = self._failures.get((trigger_id, window), 0) + 1
                self._failures[trigger_id, window] = tries
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
            self._failures.pop((trigger_id, window), None)
            # Including an overrun. The `#busy` memo said its line once, which is
            # right for one slow run — but nothing cleared it, so the NEXT time
            # this schedule overran, weeks later, the sweep stayed silent. Its
            # own comment claimed it "clears itself when the run finishes"; it
            # did not, and that is the same "a memo outlives the problem" defect
            # this module fixed for the per-row complaint, written into the fix
            # for it. Here is where the overrun demonstrably ended.
            self._said.pop((item_id, f"{path}#busy"), None)
            fired += 1

        # AFTER the loop, when every row has been graded: forget the per-row
        # complaints this file no longer makes. The per-row memo's subject is a
        # ROW, so "the file has no lint problems" is the wrong moment to clear it
        # — a row naming a workflow the app does not offer is not a lint
        # problem, so that branch runs on every tick and would erase the memo it
        # had just written, restoring the flood. And never clearing it at all is
        # the failure the memo exists to prevent: fix a bad `run:`, break it the
        # same way next week, and the sweep stays silent while the log looks
        # healthy. `#busy` is excluded — an overrun is not a complaint about the
        # file, and it clears itself when the run finishes.
        for key in [
            k
            for k in self._said
            if k[0] == item_id
            and k[1].startswith(f"{path}#")
            and not k[1].endswith("#busy")
            and k[1].rsplit("#", 1)[1] not in still_bad
        ]:
            del self._said[key]
        return fired


def _keys_in(item_id: str, path: str, raw: str) -> set[str] | None:
    """The keys a schedules file's rows would fire under, or ``None`` when the
    file does not parse as a whole — which says nothing about its rows."""
    if file_rows(raw) is None:
        return None
    folder = path.rsplit("/", 1)[0]
    return {trigger_id_for(item_id, folder, row) for row in usable_rows(raw)[0]}
