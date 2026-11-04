"""How much a deployed WUI page is used (`docs/plan-wui-usage.md`).

Two things are counted, only on a page's DEPLOYED link (U7): an **open** — the
page loaded — and a **use** — one of the three things a page asks the platform
to do (U4): run a workflow (`startRun`), call a tool (`callTool` /
`startTool`), ask an agent (`callAgent` / `startAgent`), with the time it took.

They are kept the way the tool-usage counts are (`tool_usage_store`), one row
per page per day per process: counted in memory, written back every few
minutes and on shutdown, each write leaving one revision — so the table grows
with pages × days × processes, never with traffic, and no write races another
process's. Unlike the tool counts nothing is pruned: a page's "in total" is
every day (U9), and a row per page per day stays small.

Each row keeps, per person, the opens and the uses of each kind with their
milliseconds, so a person-day (U2) is exact: the people with an open on a day,
across every process's row for it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import quote, unquote

from fastapi import Request
from msgspec import Struct, field
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

logger = logging.getLogger(__name__)

#: The three kinds of use (U4), in the order their counts sit in a cell.
KINDS = ("workflow", "tool", "agent")
#: A person's cell: opens, then (count, milliseconds) for each kind in `KINDS`.
_OPENS = 0
_WIDTH = 1 + 2 * len(KINDS)
#: The days a "last seven days" covers: today and the six before it.
WEEK = 7


def _slot(kind: str) -> int:
    if kind not in KINDS:
        raise ValueError(f"not a kind of page use: {kind!r}")
    return 1 + 2 * KINDS.index(kind)


def _today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


class WuiUsageDay(Struct, kw_only=True):
    """One process's counts for one deployed page on one day. ``by_user`` maps a
    person to their cell: opens, then a count and milliseconds per kind."""

    item_id: str
    page: str
    day: str
    pod: str
    by_user: dict[str, list[int]] = field(default_factory=dict)


def register_wui_usage(spec: SpecStar) -> None:
    """`item_id` is indexed because a page's numbers are read for the items an
    overview shows; `day` because a report reads a window of days."""
    with contextlib.suppress(ValueError):
        spec.add_model(WuiUsageDay, indexed_fields=["item_id", "day"])


def row_id(item_id: str, page: str, day: str, pod: str) -> str:
    """A specstar id holds no `/`; every part is quoted (a page path is a
    workspace path, a pod name is not ours to vouch for), `~` included, since
    `~` is the separator."""
    return "~".join(quote(p, safe="").replace("~", "%7E") for p in (item_id, page, day, pod))


def _add_cells(into: dict[str, list[int]], more: dict[str, list[int]]) -> None:
    for user, cell in more.items():
        mine = into.setdefault(user, [0] * _WIDTH)
        if len(mine) < _WIDTH:  # a row written with fewer kinds than now
            mine.extend([0] * (_WIDTH - len(mine)))
        for i, n in enumerate(cell):
            mine[i] += n


class WuiUsageRecorder:
    """This process's counts since the last flush. Thread-safe: the routes count
    on the event loop, a flush drains from a worker thread."""

    def __init__(self, *, today: Callable[[], str] = _today) -> None:
        self._today = today
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str, str], dict[str, list[int]]] = {}

    def _cell(self, item_id: str, page: str, user: str) -> list[int]:
        users = self._rows.setdefault((item_id, page, self._today()), {})
        return users.setdefault(user, [0] * _WIDTH)

    def opened(self, item_id: str, page: str, *, user: str) -> None:
        with self._lock:
            self._cell(item_id, page, user)[_OPENS] += 1

    def used(self, item_id: str, page: str, *, user: str, kind: str, ms: int) -> None:
        slot = _slot(kind)
        with self._lock:
            cell = self._cell(item_id, page, user)
            cell[slot] += 1
            cell[slot + 1] += max(int(ms), 0)

    def drain(self) -> dict[tuple[str, str, str], dict[str, list[int]]]:
        with self._lock:
            rows, self._rows = self._rows, {}
        return rows

    def merge_back(self, rows: dict[tuple[str, str, str], dict[str, list[int]]]) -> None:
        """Put rows a flush could not write back, for the next flush."""
        with self._lock:
            for key, users in rows.items():
                _add_cells(self._rows.setdefault(key, {}), users)


@dataclass(frozen=True)
class PageUsage:
    """A page's numbers (U2/U3): person-days in total and in the last seven
    days; uses and their milliseconds, likewise."""

    opened_total: int = 0
    opened_7d: int = 0
    used_total: int = 0
    used_ms_total: int = 0
    used_7d: int = 0
    used_ms_7d: int = 0


class WuiUsageStore:
    def __init__(
        self,
        spec: SpecStar,
        recorder: WuiUsageRecorder,
        *,
        pod: str,
        today: Callable[[], str] = _today,
    ) -> None:
        self._spec = spec
        self._recorder = recorder
        self._pod = pod
        self._today = today
        # One flush at a time: the stop flush waits for a periodic one.
        self._flushing = threading.Lock()

    @property
    def recorder(self) -> WuiUsageRecorder:
        return self._recorder

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not exported
        return self._spec.get_resource_manager(WuiUsageDay)

    def flush(self) -> None:
        """Write what this process counted. A row whose write fails goes back
        to the recorder for the next flush, and the failure is raised once
        every other row has been written. Blocking — call it off the loop."""
        with self._flushing:
            failed: dict[tuple[str, str, str], dict[str, list[int]]] = {}
            failure: Exception | None = None
            for key, users in self._recorder.drain().items():
                try:
                    self._add(*key, users)
                except Exception as e:  # noqa: BLE001 — kept, re-raised below
                    failure = failure or e
                    failed[key] = users
            if failed:
                self._recorder.merge_back(failed)
            if failure is not None:
                raise failure

    def _add(self, item_id: str, page: str, day: str, users: dict[str, list[int]]) -> None:
        rm = self._rm()
        rid = row_id(item_id, page, day, self._pod)
        try:
            stored = rm.get(rid).data
            assert isinstance(stored, WuiUsageDay)
        except ResourceIDNotFoundError:
            row = WuiUsageDay(item_id=item_id, page=page, day=day, pod=self._pod)
            _add_cells(row.by_user, users)
            rm.create(row, resource_id=rid)
            return
        _add_cells(stored.by_user, users)
        rm.update(rid, stored)
        # Written: a prune that fails must not put the counts back to be added
        # twice. The next write prunes this revision too.
        try:
            rm.prune_revisions(rid, keep_last_n=1)
        except Exception:  # noqa: BLE001 — logged; only an extra revision is left
            logger.exception("wui usage: prune of %s failed", rid)

    # ── reading ──────────────────────────────────────────────────────────

    def rows_of(self, item_ids: Iterable[str]) -> list[WuiUsageDay]:
        """Every row of these items (`item_id` is indexed). Blocking."""
        ids = sorted(set(item_ids))
        if not ids:
            return []
        out: list[WuiUsageDay] = []
        for res in self._rm().list_resources(QB["item_id"].in_(ids).build()):
            data = res.data
            assert isinstance(data, WuiUsageDay)
            out.append(data)
        return out

    def summary(
        self, pages: Iterable[tuple[str, str]], *, today: str | None = None
    ) -> dict[tuple[str, str], PageUsage]:
        """Each page's numbers (U2/U3), in total and over the last seven days
        ending ``today`` (default: today, UTC). A page nobody used reads as
        zeros. Blocking."""
        wanted = list(dict.fromkeys(pages))
        end = dt.date.fromisoformat(today or self._today())
        week_start = (end - dt.timedelta(days=WEEK - 1)).isoformat()
        week_end = end.isoformat()
        asked = set(wanted)
        rows = self.rows_of(i for i, _p in wanted)
        merged = _merge_days(r for r in rows if (r.item_id, r.page) in asked)
        out: dict[tuple[str, str], PageUsage] = {}
        for page in wanted:
            totals = [0] * 6
            for day, users in merged.get(page, {}).items():
                recent = week_start <= day <= week_end
                people = sum(1 for cell in users.values() if cell[_OPENS] > 0)
                uses = sum(cell[i] for cell in users.values() for i in range(1, _WIDTH, 2))
                ms = sum(cell[i] for cell in users.values() for i in range(2, _WIDTH, 2))
                totals[0] += people
                totals[2] += uses
                totals[3] += ms
                if recent:
                    totals[1] += people
                    totals[4] += uses
                    totals[5] += ms
            out[page] = PageUsage(*totals)
        return out


def _merge_days(
    rows: Iterable[WuiUsageDay],
) -> dict[tuple[str, str], dict[str, dict[str, list[int]]]]:
    """Page → day → person → cell, every process's row of a day added up — so
    a person counted by two processes is one person that day."""
    out: dict[tuple[str, str], dict[str, dict[str, list[int]]]] = {}
    for row in rows:
        _add_cells(out.setdefault((row.item_id, row.page), {}).setdefault(row.day, {}), row.by_user)
    return out


# ── what the routes call (P2/P3) ─────────────────────────────────────────

#: The header a page opened by its DEPLOYED link puts on every use, naming the
#: page (its view file's workspace path, percent-encoded). The same page opened
#: inside the item sends nothing, and its uses are not counted (U7). The SPA's
#: `DEPLOYED_HEADER` (`web/src/renderers/wui/api.ts`) is the other end.
DEPLOYED_HEADER = "X-Wui-Deployed"


class WuiRunUse(Struct, kw_only=True):
    """A run a deployed page started, until it ends (U11): which page, for
    whom, which kind, and when. Its own model, registered post-`spec.apply`,
    rather than a field on `WorkflowRun`, whose auto-CRUD has no write gate — a
    field there would let anyone write a page's counts."""

    run_id: str
    item_id: str
    page: str
    user: str
    kind: str
    started_ms: int


def register_wui_run_uses(spec: SpecStar) -> None:
    with contextlib.suppress(ValueError):
        spec.add_model(WuiRunUse)


class WuiUsage:
    """The routes' one way in: which deployed page a request names, a use timed
    in the route (`callTool`), and a run tied to a page when it starts and
    counted when it ends (`startRun`, `startTool`, `startAgent`, `callAgent`)."""

    def __init__(
        self,
        store: WuiUsageStore,
        *,
        spec: SpecStar,
        is_deployed: Callable[[str, str], bool],
        now: Callable[[], int] | None = None,
    ) -> None:
        from .timeutil import now_ms

        self._store = store
        self._spec = spec
        self._is_deployed = is_deployed
        self._now = now or now_ms
        register_wui_run_uses(spec)

    @property
    def store(self) -> WuiUsageStore:
        return self._store

    async def page_of(self, request: Request, item_id: str) -> str | None:
        """The deployed page this request came from, or None — no header, a
        path that is no path, or a page that is not Deployed (so nobody can
        grow the table with names of their own). Normalised the way the deploy
        route normalises it, so a spelling cannot split one page in two."""
        import asyncio

        from fastapi import HTTPException

        from .file_routes import _workspace_path

        raw = request.headers.get(DEPLOYED_HEADER)
        if not raw:
            return None
        try:
            path = _workspace_path(unquote(raw))
        except HTTPException:
            return None
        if not await asyncio.to_thread(self._is_deployed, item_id, path):
            return None
        return path

    def used(self, item_id: str, page: str, *, user: str, kind: str, ms: int) -> None:
        self._store.recorder.used(item_id, page, user=user, kind=kind, ms=ms)

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not exported
        return self._spec.get_resource_manager(WuiRunUse)

    def started(self, run_id: str, item_id: str, page: str, *, user: str, kind: str) -> None:
        """Tie a run to the page that started it. Blocking."""
        _slot(kind)  # an unknown kind fails here, not when the run ends
        row = WuiRunUse(
            run_id=run_id, item_id=item_id, page=page, user=user, kind=kind, started_ms=self._now()
        )
        self._rm().create(row, resource_id=run_id)

    def settled(self, run_id: str) -> None:
        """The run ended: count it, once, with the time since it started — and
        forget the tie. A run no page started is not counted. Blocking."""
        rm = self._rm()
        try:
            data = rm.get(run_id).data
            rm.permanently_delete(run_id)
        except ResourceIDNotFoundError:
            return  # not a page's run, or already counted
        assert isinstance(data, WuiRunUse)
        self.used(
            data.item_id,
            data.page,
            user=data.user,
            kind=data.kind,
            ms=self._now() - data.started_ms,
        )
