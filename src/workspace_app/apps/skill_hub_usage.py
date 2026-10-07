"""How many times each skill hub entry was installed and used
(docs/plan-skill-hub-history.md §4.8, U1–U5).

An install (of the entry's current version, by the Skills panel or the
`install_skill` tool) and a `read_skill` of a skill hub copy each add one to
THIS pod's memory — a dict increment, so nothing on the request path waits
for storage (U2). A flush, every two hours and when the pod stops, adds what
accumulated to this pod's row for the day and prunes it to one revision: a
counter that kept every revision would grow by one per flush (U3). The
total is a `Sum` over the entry's rows. Who and in which item are recorded
on the row and never shown (U5).

What a pod that dies without a SIGTERM had not flushed is lost — at most one
interval's counts. Rows are never shared between pods (the id carries the
pod), so no write here races another pod's.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import os
import re
from collections.abc import Callable, Iterable
from urllib.parse import quote

from msgspec import Struct, field
from specstar import QB, SpecStar, Sum
from specstar.types import ResourceIDNotFoundError

#: How often a pod writes what it counted (U4).
FLUSH_INTERVAL_S = 2 * 60 * 60


class UserItemUsage(Struct, kw_only=True):
    installs: int = 0
    uses: int = 0


class SkillHubUsage(Struct, kw_only=True):
    """One pod's counts for one entry on one day."""

    entry_id: str
    day: str
    pod: str
    installs: int = 0
    uses: int = 0
    #: ``"<user>∕<item id>"`` → that person's counts in that item. Recorded,
    #: never shown (U5).
    by_user_item: dict[str, UserItemUsage] = field(default_factory=dict)


def register_skill_hub_usage(spec: SpecStar) -> None:
    """Idempotently register the counter model. `entry_id` is indexed because
    the total is scoped by it; `installs` / `uses` because they are summed."""
    with contextlib.suppress(ValueError):
        spec.add_model(SkillHubUsage, indexed_fields=["entry_id", "installs", "uses"])


def pod_name() -> str:
    """This pod's name — k8s puts it in ``HOSTNAME``."""
    return os.environ.get("HOSTNAME") or "local"


#: What an entry id can be (`SkillHubRepos.path`'s rule): anything else is not one.
_ENTRY_ID = re.compile(r"[A-Za-z0-9_-]+")


def _row_id(entry_id: str, day: str, pod: str) -> str:
    # A specstar id holds no `/`; the pod name is the only part that is not
    # already a safe token.
    return f"{entry_id}~{day}~{quote(pod, safe='')}"


class UsageCounter:
    def __init__(
        self,
        spec: SpecStar,
        *,
        pod: str,
        today: Callable[[], str] = lambda: dt.datetime.now(dt.UTC).date().isoformat(),
        exists: Callable[[str], bool] = lambda _entry_id: True,
    ) -> None:
        self._spec = spec
        self._pod = pod
        self._today = today
        #: Whether an entry id names a live entry. Ids come from user-writable
        #: `.origin` files, so one that does not is dropped at the flush.
        self._exists = exists
        # One flush at a time: the shutdown flush waits for a periodic one.
        self._flushing = asyncio.Lock()
        # entry id → "<user>∕<item>" → counts not yet written.
        self._pending: dict[str, dict[str, UserItemUsage]] = {}
        self._since = ""

    def _bump(self, entry_id: str, user: str, item: str) -> UserItemUsage:
        per = self._pending.setdefault(entry_id, {})
        return per.setdefault(f"{user}∕{item}", UserItemUsage())

    def install(self, entry_id: str, *, user: str, item: str) -> None:
        self._bump(entry_id, user, item).installs += 1

    def use(self, entry_id: str, *, user: str, item: str) -> None:
        self._bump(entry_id, user, item).uses += 1

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not exported
        return self._spec.get_resource_manager(SkillHubUsage)

    async def flush(self) -> None:
        """Add what this pod counted to its rows for today. An entry whose
        write fails keeps its counts for the next flush, and the failure is
        raised once every other entry has been written.

        Shielded: cancelling the caller (the lifespan stopping the periodic
        flusher) does not cancel a flush half done — its writes run on a
        thread that a cancel cannot stop, and dropping the rest would lose
        them. The shutdown flush waits for it on the lock instead."""
        await asyncio.shield(self._flush())

    async def _flush(self) -> None:
        async with self._flushing:
            pending, self._pending = self._pending, {}
            day = self._today()
            failure: Exception | None = None
            for entry_id, per in pending.items():
                try:
                    if not _ENTRY_ID.fullmatch(entry_id) or not await asyncio.to_thread(
                        self._exists, entry_id
                    ):
                        continue
                    await asyncio.to_thread(self._add, entry_id, day, per)
                except Exception as e:  # noqa: BLE001 — kept, re-raised below
                    failure = failure or e
                    for key, counts in per.items():
                        again = self._bump(entry_id, *key.split("∕", 1))
                        again.installs += counts.installs
                        again.uses += counts.uses
            if failure is not None:
                raise failure

    def _add(self, entry_id: str, day: str, per: dict[str, UserItemUsage]) -> None:
        rm = self._rm()
        row_id = _row_id(entry_id, day, self._pod)
        try:
            row = rm.get(row_id).data
            assert isinstance(row, SkillHubUsage)
            exists = True
        except ResourceIDNotFoundError:
            row, exists = SkillHubUsage(entry_id=entry_id, day=day, pod=self._pod), False
        for key, counts in per.items():
            mine = row.by_user_item.setdefault(key, UserItemUsage())
            mine.installs += counts.installs
            mine.uses += counts.uses
            row.installs += counts.installs
            row.uses += counts.uses
        if exists:
            rm.update(row_id, row)
            rm.prune_revisions(row_id, keep_last_n=1)
        else:
            rm.create(row, resource_id=row_id)

    def totals(self, entry_ids: Iterable[str]) -> dict[str, tuple[int, int]]:
        """``{entry id: (installs, uses)}`` as written so far — one grouped
        sum bounded to these entries, never a scan of the table."""
        ids = list(dict.fromkeys(entry_ids))
        out = dict.fromkeys(ids, (0, 0))
        if not ids:
            return out
        groups = self._rm().exp_aggregate_by(
            by=QB["entry_id"],
            aggregates={"installs": Sum(QB["installs"]), "uses": Sum(QB["uses"])},
            query=QB["entry_id"].in_(ids).build(),
        )
        for g in groups:
            out[g.key] = (int(g["installs"] or 0), int(g["uses"] or 0))
        return out

    def counted_since(self) -> str:
        """The first day any pod wrote a count (`YYYY-MM-DD`), or "" before
        that. Once found it never changes, so it is asked of the store once."""
        if not self._since:
            first = QB.all().sort(QB.created_time().asc(), QB.resource_id().asc()).limit(1).build()
            for res in self._rm().list_resources(first, returns=["data"]):
                assert isinstance(res.data, SkillHubUsage)
                self._since = res.data.day
        return self._since
