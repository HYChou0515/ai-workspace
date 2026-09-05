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
would catch one and miss the other.

There is no single chokepoint to hook — that was the first version's mistake,
and it cost the feature entirely: the hook went into ``_write_unchecked``, which
``write_from_path`` (the PUT route's streaming upload, i.e. how a page saves)
does not go through, so the one write this index exists to notice was the one it
never saw. The facade now calls ``_landed`` from every path that lands bytes,
and ``tests/api/test_schedule_index.py`` derives that set from the facade's own
source so a new one cannot be added silently.

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
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from ..sync.ignore import DEFAULT_IGNORES, should_ignore

logger = logging.getLogger(__name__)

#: The file a page writes its schedules into, inside its own folder.
SCHEDULES_FILE = "schedules.json"


class _ScheduleIndex(Struct):
    """``resource_id == item_id``, so a lookup is a point read and the sweep's
    listing is the whole (short) table."""

    paths: list[str] = []


#: Bounded like the trigger ledger's: a loop that cannot end is worse than a
#: refused write, and only pathological churn ever reaches the last try.
_MAX_CAS_RETRIES = 100


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
    a page is a folder, and a view file at the root has no folder of its own —
    it cannot write, so it cannot be a page.

    ANY depth below that does. Nothing says a page's folder sits at the top:
    `wuiFolder` accepts any depth and `writeFile`'s boundary is the page's own
    folder, so a page at `/reports/scrap/` writes
    `/reports/scrap/schedules.json` quite legitimately. Requiring exactly two
    segments dropped that on the floor with no error anywhere — the page saved,
    saw its file, and nothing ever ran.
    """
    parts = path.strip("/").split("/")
    if len(parts) <= 1 or parts[-1] != SCHEDULES_FILE:
        return False
    # Not inside a derivative. `node_modules/`, `.venv/` and the caches hold
    # other people's files, and a vendored or unpacked `schedules.json` there is
    # not a declaration anybody made — it would fire work nobody asked for, from
    # a folder they have never opened. The list is the mirror's, shared rather
    # than re-spelled: a file the platform declines to BACK UP is not one it
    # should take instructions from, and one list cannot disagree with itself.
    return not should_ignore(path, DEFAULT_IGNORES)


class ScheduleIndex:
    """The short list of items whose pages declare schedules."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _res(self, item_id: str) -> tuple[_ScheduleIndex, str] | None:
        """The row AND its etag, because every write here is a CAS."""
        rm = self._spec.get_resource_manager(_ScheduleIndex)
        try:
            res = rm.get(item_id)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        data = res.data
        if not isinstance(data, _ScheduleIndex):  # pragma: no cover - defensive
            return None
        return data, res.info.etag

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
        try:
            # First writer wins. Narrow, not `except Exception`: "somebody got
            # here first" and "the store is broken" used to produce the same
            # silence, and the second one leaves the item with NO row — which the
            # sweep reads as "this item has no schedules". That is the exact
            # failure this module exists to prevent, arriving through its own
            # error handling.
            rm.create(_ScheduleIndex(paths=[path]), resource_id=item_id, if_not_exists=True)  # ty: ignore[unknown-argument]
            return True
        except DuplicateResourceError:
            pass  # a row exists — CAS-merge into it below

        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = self._res(item_id)
            except ResourceIsDeletedError:  # pragma: no cover - nothing deletes these now
                # RESTORE, never recurse. `create(if_not_exists=True)` raises
                # `DuplicateResourceError` for a soft-deleted id too (existence is
                # deletion-blind by contract), so a self-call here loops until the
                # stack ends — a thousand round trips, swallowed by `_landed`'s
                # `except Exception`, with the index quietly never updated. The
                # pattern this was copied from (`SpecstarTriggerStore.try_claim`)
                # has exactly this branch; dropping it was the whole bug.
                rm.restore(item_id)
                continue
            if res is None:
                # Genuinely absent between the create and the read.
                res = self._res(item_id)
                if res is None:
                    rm.create(_ScheduleIndex(paths=[path]), resource_id=item_id)
                    return True
            row, etag = res
            if path in row.paths:
                return False
            try:
                rm.modify(
                    item_id,
                    _ScheduleIndex(paths=sorted({*row.paths, path})),
                    status=RevisionStatus.draft,
                    expected_etag=etag,  # ty: ignore[unknown-argument]
                )
                return True
            except PreconditionFailedError:
                # A peer merged between our read and our write. Read-modify-write
                # without this loses whichever path lost the race, permanently:
                # nothing re-adds it but a WRITE of that page's file, and the
                # sweep has no way to know it is missing.
                continue
        raise RuntimeError(  # pragma: no cover - only under pathological churn
            f"schedule index CAS exhausted retries for {item_id!r}"
        )

    def forget(self, item_id: str, path: str) -> None:
        """Drop a path the sweep could not read. Quiet when the row is already
        gone: two pods may sweep the same item at once.

        The row is EMPTIED, not deleted — `delete` takes no etag, so a delete
        racing a `record` takes the peer's new path with it. An item with no
        paths left is skipped by `items()`, so it stops being read either way,
        which is the property that matters: otherwise the sweep pays for it on
        every pass forever.
        """
        rm = self._spec.get_resource_manager(_ScheduleIndex)
        for _ in range(_MAX_CAS_RETRIES):
            res = self._res(item_id)
            if res is None:
                return
            row, etag = res
            if path not in row.paths:
                return
            try:
                with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
                    # Emptied, never DELETED. `delete` takes no etag, so a delete
                    # racing a `record` takes the peer's freshly-added path with
                    # it and nothing ever puts it back. An empty row is a CAS
                    # write like any other, and `items()` skips it — so the item
                    # stops being swept either way, which was the whole point.
                    # The cost is one tiny row per item that ever had a schedule,
                    # a set bounded by design.
                    rm.modify(
                        item_id,
                        _ScheduleIndex(paths=[p for p in row.paths if p != path]),
                        status=RevisionStatus.draft,
                        expected_etag=etag,  # ty: ignore[unknown-argument]
                    )
                return
            except PreconditionFailedError:
                continue  # a peer changed the row — re-read and decide again

    def items(self) -> list[str]:
        """Every item with at least one schedule file. The sweep's whole input."""
        rm = self._spec.get_resource_manager(_ScheduleIndex)
        # `is_deleted() == False` because `list_resources` happily returns
        # soft-deleted rows. Without it an item whose last schedule was removed
        # keeps being read on every sweep, forever — which is the one cost this
        # index exists to avoid.
        query = (QB.is_deleted() == False).build()  # noqa: E712
        out: list[str] = []
        for res in rm.list_resources(query, returns=["data", "info"]):
            data = res.data
            # An EMPTIED row is not an item to sweep. `forget` empties rather
            # than deletes, because a delete cannot be made conditional and so
            # races a concurrent `record` — it would take the peer's freshly
            # added path with it, and nothing puts that back.
            #
            # ⚠️ THE COST, stated rather than implied away: the row survives, so
            # this listing fetches one row per item that has EVER had a schedule,
            # every tick, for the life of the deployment. The old delete removed
            # it at the query level. That set is bounded by "items that ever
            # scheduled something", which is the set this index was designed to
            # hold — but nothing reclaims it, and if it ever stops being small
            # the answer is an indexed flag to filter on, not a racy delete.
            if isinstance(data, _ScheduleIndex) and data.paths:
                out.append(res.info.resource_id)  # ty: ignore[unresolved-attribute]
        return sorted(out)

    def paths(self, item_id: str) -> list[str]:
        row = self._row(item_id)
        return list(row.paths) if row else []
