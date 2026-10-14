"""Where the tool-call counts are written, and how long they are kept
(docs/plan-tool-usage-stats.md §3.5, D5).

Each pod adds what its `ToolUsageRecorder` counted to its OWN row per tool,
source and day — the row id carries the pod, so two pods never write one row
and no compare-and-swap is needed (the skill hub counter's shape). A flush runs
every few minutes and once more when the process stops; what a pod that dies
without stopping had not flushed is lost — at most one interval of counts.

Rows older than `KEEP_DAYS` are deleted, once a day per pod, by an indexed
query on `day` — never a scan of the table (#804).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import threading
from collections.abc import Callable
from urllib.parse import quote

from msgspec import Struct, field
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..agent.tool_usage import SEP, ToolUsageRecorder, ToolUsageRow, add_row

# Re-exported: the API and the workers name their rows with it.
from ..apps.skill_hub_usage import writer_name as writer_name

logger = logging.getLogger(__name__)

#: How long the counts are kept (D5).
KEEP_DAYS = 90

#: How often a process writes what it counted (§3.5).
FLUSH_INTERVAL_S = 5 * 60


class ToolUsageDay(Struct, kw_only=True):
    """One process's sums for one tool — or one skill (`kind`, plan §8.3) —
    from one source on one day. Each breakdown maps a key — a person;
    `"<surface>∕<app>"`; an item id — to `[calls, fails, total_ms]`."""

    tool: str
    source: str
    kind: str = "tool"
    #: The package a command belongs to (§9.1); "" for a built-in or a skill.
    package: str = ""
    day: str
    pod: str
    calls: int = 0
    fails: int = 0
    total_ms: int = 0
    by_user: dict[str, list[int]] = field(default_factory=dict)
    by_surface_app: dict[str, list[int]] = field(default_factory=dict)
    by_item: dict[str, list[int]] = field(default_factory=dict)


def register_tool_usage(spec: SpecStar) -> None:
    """Idempotently register the model. `day` is indexed because both the
    window a report asks for and the prune are ranges on it; `tool` because a
    tool's own breakdown is read by it; `kind` because a report reads one."""
    with contextlib.suppress(ValueError):
        spec.add_model(ToolUsageDay, indexed_fields=["day", "tool", "kind", "package"])


def _today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


def _row_id(kind: str, tool: str, source: str, day: str, pod: str) -> str:
    # A specstar id holds no `/`; every part is quoted, since a third-party
    # tool name, a skill name and a pod name are not ours to vouch for.
    return "~".join(quote(p, safe="") for p in (kind, tool, source, day, pod))


class ToolUsageStore:
    def __init__(
        self,
        spec: SpecStar,
        recorder: ToolUsageRecorder,
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
        self._pruned_on = ""

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not exported
        return self._spec.get_resource_manager(ToolUsageDay)

    def flush(self) -> None:
        """Write what this process counted; prune once a day. A row whose write
        fails goes back to the recorder for the next flush, and the failure is
        raised once every other row has been written. Blocking — call it off
        the event loop."""
        with self._flushing:
            failed: list[ToolUsageRow] = []
            failure: Exception | None = None
            for row in self._recorder.drain():
                try:
                    self._add(row)
                except Exception as e:  # noqa: BLE001 — kept, re-raised below
                    failure = failure or e
                    failed.append(row)
            if failed:
                self._recorder.merge_back(failed)
            today = self._today()
            if self._pruned_on != today:
                self._pruned_on = today
                try:
                    self.prune()
                except Exception:  # noqa: BLE001 — tried again tomorrow
                    logger.exception("tool usage: prune failed")
            if failure is not None:
                raise failure

    def _add(self, row: ToolUsageRow) -> None:
        rm = self._rm()
        row_id = _row_id(row.kind, row.tool, row.source, row.day, self._pod)
        try:
            stored = rm.get(row_id).data
            assert isinstance(stored, ToolUsageDay)
        except ResourceIDNotFoundError:
            stored = ToolUsageDay(
                tool=row.tool,
                source=row.source,
                kind=row.kind,
                package=row.package,
                day=row.day,
                pod=self._pod,
            )
            add_row(stored, row)
            rm.create(stored, resource_id=row_id)
            return
        add_row(stored, row)
        rm.update(row_id, stored)
        # Written: a prune that fails must not put the counts back to be added
        # twice. The next write prunes this revision too.
        try:
            rm.prune_revisions(row_id, keep_last_n=1)
        except Exception:  # noqa: BLE001 — logged; only an extra revision is left
            logger.exception("tool usage: prune of %s failed", row_id)

    def prune(self) -> None:
        """Delete every row older than `KEEP_DAYS` (D5) — an indexed range on
        `day`, so only the expired rows are read. Permanently: a soft delete
        keeps the row and every revision, and the table would grow anyway."""
        cutoff = (dt.date.fromisoformat(self._today()) - dt.timedelta(days=KEEP_DAYS)).isoformat()
        rm = self._rm()
        old = QB["day"] < cutoff
        for meta in rm.search_resources(query=old.build()):
            # Another process's prune of the same day may have got there first.
            with contextlib.suppress(ResourceIDNotFoundError):
                rm.permanently_delete(meta.resource_id)


def run_flusher(store: ToolUsageStore | object, stop: threading.Event, interval: float) -> None:
    """A worker process's flush loop: every `interval` seconds until `stop`,
    then once more — the counts its agents made since the last one."""
    flush = store.flush  # ty: ignore[unresolved-attribute]
    while not stop.wait(interval):
        try:
            flush()
        except Exception:  # noqa: BLE001 — kept for the next flush
            logger.exception("tool usage: flush failed; kept for the next one")
    try:
        flush()
    except Exception:  # noqa: BLE001 — the process is stopping; nothing to retry with
        logger.exception("tool usage: final flush failed")


# ── reading: what the administrator's report asks (§3.6) ───────────────────


def since_day(days: int, today: str | None = None) -> str:
    """The first day of a `days`-long window ending today (inclusive)."""
    end = dt.date.fromisoformat(today or _today())
    return (end - dt.timedelta(days=days - 1)).isoformat()


def rows_since(
    spec: SpecStar,
    since: str,
    *,
    tool: str | None = None,
    kind: str = "tool",
    package: str | None = None,
) -> list[ToolUsageDay]:
    """Every row of one `kind` from `since` on — an indexed range on `day` (and
    `tool` / `package` when one is named), never the whole table."""
    q = (QB["day"] >= since) & (QB["kind"] == kind)
    if tool is not None:
        q = q & (QB["tool"] == tool)
    if package is not None:
        q = q & (QB["package"] == package)
    out: list[ToolUsageDay] = []
    for res in spec.get_resource_manager(ToolUsageDay).list_resources(q.build(), returns=["data"]):
        if isinstance(res.data, ToolUsageDay):
            out.append(res.data)
    return out


def _sum_cells(cells: list[list[int]]) -> tuple[int, int, int]:
    return (sum(c[0] for c in cells), sum(c[1] for c in cells), sum(c[2] for c in cells))


def group_name(row: ToolUsageDay, group: str) -> str:
    """What a row is reported under: its tool, or — by package (§9.1) — its
    package; a built-in has none, so it stays itself."""
    return row.package if group == "package" and row.package else row.tool


def per_tool(
    rows: list[ToolUsageDay],
    *,
    source: str = "",
    surface: str = "",
    app: str = "",
    group: str = "command",
) -> list[tuple[str, str, int, int, int]]:
    """`(name, source, calls, fails, total_ms)` per tool — or per package
    (`group`) — every pod and day added. Filtering by surface or App reads the
    tool's surface × App breakdown, since a row's totals cover every surface."""
    sums: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        if source and row.source != source:
            continue
        if surface or app:
            picked = [
                cell
                for key, cell in row.by_surface_app.items()
                if (not surface or key.split(SEP, 1)[0] == surface)
                and (not app or key.split(SEP, 1)[-1] == app)
            ]
            calls, fails, ms = _sum_cells(picked)
        else:
            calls, fails, ms = row.calls, row.fails, row.total_ms
        if not calls:
            continue
        acc = sums.setdefault((group_name(row, group), row.source), [0, 0, 0])
        acc[0] += calls
        acc[1] += fails
        acc[2] += ms
    return [(t, s, c, f, m) for (t, s), (c, f, m) in sums.items()]


def breakdown(rows: list[ToolUsageDay], name: str) -> dict[str, tuple[int, int, int]]:
    """One breakdown (`by_user` / `by_surface_app` / `by_item`) of `rows`,
    every pod and day added: key → (calls, fails, total_ms)."""
    sums: dict[str, list[int]] = {}
    for row in rows:
        for key, (calls, fails, ms) in getattr(row, name).items():
            acc = sums.setdefault(key, [0, 0, 0])
            acc[0] += calls
            acc[1] += fails
            acc[2] += ms
    return {k: (c, f, m) for k, (c, f, m) in sums.items()}


#: The raw export's columns (§9.3), in order.
RAW_COLUMNS = (
    "day",
    "kind",
    "name",
    "package",
    "source",
    "dimension",
    "user",
    "surface",
    "app",
    "item",
    "calls",
    "fails",
    "total_ms",
)


def _raw_line(
    base: dict[str, str | int], dimension: str, cells: tuple[int, int, int], **who: str
) -> dict[str, str | int]:
    return {
        **base,
        "dimension": dimension,
        "user": who.get("user", ""),
        "surface": who.get("surface", ""),
        "app": who.get("app", ""),
        "item": who.get("item", ""),
        "calls": cells[0],
        "fails": cells[1],
        "total_ms": cells[2],
    }


def raw_lines(rows: list[ToolUsageDay]) -> list[dict[str, str | int]]:
    """Every stored cell of `rows`, one line each (§9.3): a row's total and each
    of its three breakdowns — the processes' rows of one day added, since which
    process counted a call is ours, not the reader's. Ordered by day, then name."""
    by_day: dict[tuple[str, str, str, str, str], ToolUsageRow] = {}
    for row in rows:
        key = (row.day, row.kind, row.tool, row.source, row.package)
        acc = by_day.setdefault(
            key, ToolUsageRow(row.day, row.tool, row.source, row.kind, row.package)
        )
        add_row(acc, row)  # ty: ignore[invalid-argument-type]
    out: list[dict[str, str | int]] = []
    for (day, kind, tool, source, package), r in sorted(by_day.items()):
        base: dict[str, str | int] = {
            "day": day,
            "kind": kind,
            "name": tool,
            "package": package,
            "source": source,
        }
        out.append(_raw_line(base, "total", (r.calls, r.fails, r.total_ms)))
        for user, (c, f, m) in sorted(r.by_user.items()):
            out.append(_raw_line(base, "user", (c, f, m), user=user))
        for key, (c, f, m) in sorted(r.by_surface_app.items()):
            surface, _, app = key.partition(SEP)
            out.append(_raw_line(base, "surface_app", (c, f, m), surface=surface, app=app))
        for item, (c, f, m) in sorted(r.by_item.items()):
            out.append(_raw_line(base, "item", (c, f, m), item=item))
    return out
