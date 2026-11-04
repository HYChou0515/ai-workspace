"""How much a deployed WUI page is used (`docs/plan-wui-usage.md`).

Two things are counted, only on a page's DEPLOYED link (U7): an **open** — the
page loaded — and a **use** — one of the three things a page asks the platform
to do (U4): run a workflow (`startRun`), call a tool (`callTool` /
`startTool`), ask an agent (`callAgent` / `startAgent`), with the time it took.

Counted in memory, as the tool-usage counts are (`tool_usage_store`), and
written back every few minutes and on shutdown — into ONE row per page per day
(U9), whichever process counted it: each process merges into it with a
compare-and-swap on the row's etag, so the table grows with pages × days,
never with traffic, processes or restarts (review round 1: a row per process
multiplied with every rollout). Each write leaves one revision. Nothing is
pruned: a page's "in total" is every day.

Each day row keeps, per person, the opens and the uses of each kind with their
milliseconds, so a person-day (U2) is exact: the people with an open that day.
Beside them, ONE total row per page (`WuiUsageTotal`) gains what each write
added — new person-days, uses, milliseconds — so the overview's "in total"
reads one row per page and the last week's days, never every day (review
round 1: every day of every page on every overview load).
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
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
)

logger = logging.getLogger(__name__)

#: The three kinds of use (U4), in the order their counts sit in a cell.
KINDS = ("workflow", "tool", "agent")
#: A person's cell: opens, then (count, milliseconds) for each kind in `KINDS`.
_OPENS = 0
_WIDTH = 1 + 2 * len(KINDS)
#: The days a "last seven days" covers: today and the six before it.
WEEK = 7
#: How often a write retries against a row another process changed meanwhile.
_TRIES = 8


def _slot(kind: str) -> int:
    if kind not in KINDS:
        raise ValueError(f"not a kind of page use: {kind!r}")
    return 1 + 2 * KINDS.index(kind)


def _today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


class WuiUsageDay(Struct, kw_only=True):
    """One deployed page's counts on one day, every process's added in.
    ``by_user`` maps a person to their cell: opens, then a count and
    milliseconds per kind."""

    item_id: str
    page: str
    day: str
    by_user: dict[str, list[int]] = field(default_factory=dict)


class WuiUsageTotal(Struct, kw_only=True):
    """One deployed page's numbers over every day: person-days, uses and their
    milliseconds — what the overview's "in total" reads."""

    item_id: str
    page: str
    person_days: int = 0
    uses: int = 0
    uses_ms: int = 0


def register_wui_usage(spec: SpecStar) -> None:
    """`item_id` is indexed because a page's numbers are read for the items an
    overview shows; `day` because a report reads a window of days."""
    with contextlib.suppress(ValueError):
        spec.add_model(WuiUsageDay, indexed_fields=["item_id", "day"])
    with contextlib.suppress(ValueError):
        spec.add_model(WuiUsageTotal, indexed_fields=["item_id"])


def row_id(item_id: str, page: str, day: str) -> str:
    """A specstar id holds no `/`; every part is quoted (a page path is a
    workspace path), `~` included, since `~` is the separator. The total row is
    the day ``"total"``, which no ISO date spells."""
    return "~".join(quote(p, safe="").replace("~", "%7E") for p in (item_id, page, day))


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
        today: Callable[[], str] = _today,
    ) -> None:
        self._spec = spec
        self._recorder = recorder
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
        """Merge ``users`` into the page's row for ``day`` — against the etag
        it was read with, so a process writing meanwhile is not overwritten:
        a refused write reads again and redoes it — then add what it changed
        to the page's total. Raises when the DAY row could not be written (the
        counts go back for the next flush); a total that could not be written
        is logged, never retried with the day's counts, which are in already."""
        rm = self._rm()
        rid = row_id(item_id, page, day)
        for _ in range(_TRIES):
            try:
                res = rm.get(rid)
            except ResourceIDNotFoundError:
                res = None
            before = res.data.by_user if res is not None else {}
            assert isinstance(before, dict)
            after = {u: list(c) for u, c in before.items()}
            _add_cells(after, users)
            try:
                if res is None:
                    rm.create(
                        WuiUsageDay(item_id=item_id, page=page, day=day, by_user=after),
                        resource_id=rid,
                    )
                else:
                    row = res.data
                    assert isinstance(row, WuiUsageDay)
                    row.by_user = after
                    rm.update(rid, row, expected_etag=res.info.etag)
            except (PreconditionFailedError, DuplicateResourceError):
                continue  # another process wrote it meanwhile: read it again
            self._prune(rm, rid)
            people = sum(
                1
                for u, cell in after.items()
                if cell[_OPENS] > 0 and before.get(u, [0])[_OPENS] == 0
            )
            uses = sum(c[i] for c in users.values() for i in range(1, len(c), 2))
            ms = sum(c[i] for c in users.values() for i in range(2, len(c), 2))
            try:
                self._add_total(item_id, page, people, uses, ms)
            except Exception:  # noqa: BLE001 — the day is written; never add it twice
                logger.exception("wui usage: total of %s %s not updated", item_id, page)
            return
        raise RuntimeError(f"wui usage: {rid} kept changing under {_TRIES} writes")

    def _add_total(self, item_id: str, page: str, people: int, uses: int, ms: int) -> None:
        rm = self._spec.get_resource_manager(WuiUsageTotal)
        rid = row_id(item_id, page, "total")
        for _ in range(_TRIES):
            try:
                res = rm.get(rid)
            except ResourceIDNotFoundError:
                res = None
            try:
                if res is None:
                    rm.create(
                        WuiUsageTotal(
                            item_id=item_id, page=page, person_days=people, uses=uses, uses_ms=ms
                        ),
                        resource_id=rid,
                    )
                else:
                    row = res.data
                    assert isinstance(row, WuiUsageTotal)
                    row.person_days += people
                    row.uses += uses
                    row.uses_ms += ms
                    rm.update(rid, row, expected_etag=res.info.etag)  # ty: ignore[unknown-argument]
            except (PreconditionFailedError, DuplicateResourceError):
                continue
            self._prune(rm, rid)
            return
        raise RuntimeError(f"wui usage: {rid} kept changing under {_TRIES} writes")

    @staticmethod
    def _prune(rm, rid: str) -> None:  # noqa: ANN001 — specstar's manager type
        # Written: a prune that fails must not put the counts back to be added
        # twice. The next write prunes this revision too.
        try:
            rm.prune_revisions(rid, keep_last_n=1)
        except Exception:  # noqa: BLE001 — logged; only an extra revision is left
            logger.exception("wui usage: prune of %s failed", rid)

    # ── reading ──────────────────────────────────────────────────────────

    def summary(
        self, pages: Iterable[tuple[str, str]], *, today: str | None = None
    ) -> dict[tuple[str, str], PageUsage]:
        """Each page's numbers (U2/U3): in total, from its total row; over the
        last seven days ending ``today`` (default: today, UTC), from those
        days' rows. Two indexed reads, bounded by pages and by the week — never
        every day. A page nobody used reads as zeros. Blocking."""
        from .tool_usage_store import days_between

        wanted = list(dict.fromkeys(pages))
        ids = sorted({i for i, _p in wanted})
        if not ids:
            return {}
        end = dt.date.fromisoformat(today or self._today())
        week = days_between((end - dt.timedelta(days=WEEK - 1)).isoformat(), end.isoformat())
        totals: dict[tuple[str, str], WuiUsageTotal] = {}
        trm = self._spec.get_resource_manager(WuiUsageTotal)
        for res in trm.list_resources(QB["item_id"].in_(ids).build()):
            data = res.data
            assert isinstance(data, WuiUsageTotal)
            totals[(data.item_id, data.page)] = data
        recent = _merge_days(
            res.data
            for res in self._rm().list_resources(
                (QB["item_id"].in_(ids) & QB["day"].in_(week)).build()
            )
            if isinstance(res.data, WuiUsageDay)
        )
        out: dict[tuple[str, str], PageUsage] = {}
        for page in wanted:
            total = totals.get(page)
            week_people = week_uses = week_ms = 0
            for users in recent.get(page, {}).values():
                week_people += sum(1 for cell in users.values() if cell[_OPENS] > 0)
                week_uses += sum(c[i] for c in users.values() for i in range(1, _WIDTH, 2))
                week_ms += sum(c[i] for c in users.values() for i in range(2, _WIDTH, 2))
            out[page] = PageUsage(
                opened_total=total.person_days if total else 0,
                opened_7d=week_people,
                used_total=total.uses if total else 0,
                used_ms_total=total.uses_ms if total else 0,
                used_7d=week_uses,
                used_ms_7d=week_ms,
            )
        return out


def _merge_days(
    rows: Iterable[WuiUsageDay],
) -> dict[tuple[str, str], dict[str, dict[str, list[int]]]]:
    """Page → day → person → cell. A day has one row; adding them up still
    keeps a person one person that day if two rows ever named the same day."""
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
    whom, which kind; its time is the run record's own. Its own model,
    registered post-`spec.apply`, rather than a field on `WorkflowRun`, whose
    auto-CRUD has no write gate — a field there would let anyone write a page's
    counts."""

    run_id: str
    item_id: str
    page: str
    user: str
    kind: str


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
    ) -> None:
        self._store = store
        self._spec = spec
        self._is_deployed = is_deployed
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
        row = WuiRunUse(run_id=run_id, item_id=item_id, page=page, user=user, kind=kind)
        self._rm().create(row, resource_id=run_id)

    def settled(self, run_id: str) -> None:
        """The run ended: count it, once, and forget the tie. A run no page
        started is not counted. Blocking."""
        rm = self._rm()
        try:
            data = rm.get(run_id).data
            rm.permanently_delete(run_id)
        except ResourceIDNotFoundError:
            return  # not a page's run, or already counted
        assert isinstance(data, WuiRunUse)
        self.used(data.item_id, data.page, user=data.user, kind=data.kind, ms=self._took(run_id))

    def _took(self, run_id: str) -> int:
        """The run's own `started → ended` (review round 1, R3): the wait for a
        slot is not time spent on anyone's behalf, and a page job ends at its
        deadline however late it was found lost. 0 for a run that never
        started, or whose record is gone."""
        from specstar.types import ResourceIsDeletedError

        from ..workflow.run import WorkflowRun

        try:
            run = self._spec.get_resource_manager(WorkflowRun).get(run_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return 0
        assert isinstance(run, WorkflowRun)
        if run.started is None or run.ended is None:
            return 0
        end = run.ended
        if run.page_job is not None and run.page_job.deadline:
            end = min(end, run.page_job.deadline)
        return max(0, end - run.started)


# ── the administrator's report (P5, U8/U12) ──────────────────────────────


def rows_in_window(spec: SpecStar, days: int, *, today: str | None = None) -> list[WuiUsageDay]:
    """Every row of the last ``days`` days ending today — or, for 0, of every
    day (U12). The window's days by name on the indexed `day`, never a range
    (`tool_usage_store.days_between`: a range there failed on Postgres), with
    two days past today for a process whose clock runs ahead. Blocking."""
    from .tool_usage_store import days_between

    rm = spec.get_resource_manager(WuiUsageDay)
    if days == 0:
        query = QB.all()
    else:
        end = dt.date.fromisoformat(today or _today())
        first = (end - dt.timedelta(days=days - 1)).isoformat()
        query = QB["day"].in_(days_between(first, (end + dt.timedelta(days=2)).isoformat()))
    out: list[WuiUsageDay] = []
    for res in rm.list_resources(query.build()):
        data = res.data
        assert isinstance(data, WuiUsageDay)
        out.append(data)
    return out


@dataclass(frozen=True)
class PageLine:
    """One page's line of the report: person-days, and each kind's uses and
    milliseconds (`[workflow n, ms, tool n, ms, agent n, ms]`)."""

    item_id: str
    page: str
    person_days: int
    cells: tuple[int, ...]


def per_page(rows: Iterable[WuiUsageDay]) -> list[PageLine]:
    """One line per page, busiest first (person-days, then uses)."""
    lines: list[PageLine] = []
    for (item_id, page), days in _merge_days(rows).items():
        people = 0
        cells = [0] * (_WIDTH - 1)
        for users in days.values():
            people += sum(1 for cell in users.values() if cell[_OPENS] > 0)
            for cell in users.values():
                for i in range(1, _WIDTH):
                    cells[i - 1] += cell[i]
        lines.append(PageLine(item_id, page, people, tuple(cells)))
    lines.sort(key=lambda x: (-x.person_days, -sum(x.cells[0::2]), x.item_id, x.page))
    return lines


@dataclass(frozen=True)
class PersonLine:
    """One person's part of a page: the days they opened it, how often, and
    each kind's uses and milliseconds."""

    user: str
    person_days: int
    opens: int
    cells: tuple[int, ...]


def per_person(rows: Iterable[WuiUsageDay], item_id: str, page: str) -> list[PersonLine]:
    """A page's people, most person-days first."""
    merged = _merge_days(rows)
    acc: dict[str, list[int]] = {}
    for users in merged.get((item_id, page), {}).values():
        for user, cell in users.items():
            mine = acc.setdefault(user, [0] * (_WIDTH + 1))
            mine[0] += 1 if cell[_OPENS] > 0 else 0
            for i, n in enumerate(cell):
                mine[i + 1] += n
    out = [PersonLine(u, c[0], c[1], tuple(c[2:])) for u, c in acc.items()]
    out.sort(key=lambda x: (-x.person_days, -x.opens, x.user))
    return out


#: The CSV's columns: one line per person per page per day, every process's
#: row of that day added up.
RAW_COLUMNS = (
    "day",
    "item_id",
    "page",
    "user",
    "opens",
    # Each kind's count and milliseconds, in `KINDS` order — as a cell holds them.
    "workflow",
    "workflow_ms",
    "tool",
    "tool_ms",
    "agent",
    "agent_ms",
)


def raw_lines(rows: Iterable[WuiUsageDay]) -> list[dict[str, str | int]]:
    """Every person's day of every page — the raw cells behind the report."""
    from .tool_usage_store import _as_text

    out: list[dict[str, str | int]] = []
    for (item_id, page), days in sorted(_merge_days(rows).items()):
        for day, users in sorted(days.items()):
            for user, cell in sorted(users.items()):
                line: dict[str, str | int] = {
                    "day": day,
                    "item_id": _as_text(item_id),
                    "page": _as_text(page),
                    "user": _as_text(user),
                    "opens": cell[_OPENS],
                }
                for i, col in enumerate(RAW_COLUMNS[5:]):
                    line[col] = cell[1 + i]
                out.append(line)
    return out
