"""Bytes reach the durable store by more doors than a hook can be nailed to.

P40 nailed one to the mirror because the facade was not enough. It is still not
enough: `registry._writeback` returns before `sync.mirror` on a host-managed
deployment, so on that branch the mirror hook never fires and P40's fix does
nothing. And a third writer exists already — `apps.seeding.seed_item` and the
`/collections.json` route write straight to the raw filestore, past both.

A hook per door is a list of doors, and a list goes stale the way every
hand-written list in this branch has. So the index gets a RECONCILER instead:
at the turn boundary, ask what schedule files the item has and record them. It
is idempotent (`record` is a CAS merge), it is right on both deployments, and a
door added tomorrow is covered without anybody remembering this file.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar

from workspace_app.api.app import _reconcile_after_turn
from workspace_app.api.schedule_index import (
    SCHEDULES_FILE,
    ScheduleIndex,
    register_schedule_index,
)
from workspace_app.api.schedule_reconcile import reconcile_item_schedules
from workspace_app.resources import make_spec

ITEM = "i1"


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_schedule_index(s)
    return s


async def test_a_schedule_file_no_hook_saw_is_still_indexed(spec: SpecStar) -> None:
    """The whole point. Nothing told the index; the listing did."""
    index = ScheduleIndex(spec)

    async def _ls(item_id: str) -> list[str]:
        return [f"/report/{SCHEDULES_FILE}", "/report/index.html", "/notes.txt"]

    await reconcile_item_schedules(ITEM, ls=_ls, index=index)

    assert index.paths(ITEM) == [f"/report/{SCHEDULES_FILE}"]


async def test_it_records_only_what_counts_as_a_declaration(spec: SpecStar) -> None:
    """The control. A reconciler that indexed everything would satisfy the test
    above completely, and would then sweep an item for every file it owns.

    The rule is `is_schedule_file`'s, shared rather than re-spelled: exact
    filename, never at the workspace root, and not inside a derivative folder a
    user did not author.
    """
    index = ScheduleIndex(spec)

    async def _ls(item_id: str) -> list[str]:
        return [
            f"/{SCHEDULES_FILE}",  # root: no folder of its own, cannot be a page
            f"/node_modules/pkg/{SCHEDULES_FILE}",  # vendored, nobody declared it
            f"/page/{SCHEDULES_FILE}.bak",  # not the file
            "/page/schedules.yaml",
        ]

    await reconcile_item_schedules(ITEM, ls=_ls, index=index)

    assert index.paths(ITEM) == []


async def test_it_adds_without_dropping_what_the_hooks_already_recorded(
    spec: SpecStar,
) -> None:
    """Idempotent and additive, because it runs BESIDE the hooks, not instead.

    A reconcile that replaced the row would race the mirror hook: whichever
    finished last would win, and a page saved during a turn could be dropped by
    a reconcile that listed the workspace a moment earlier.
    """
    index = ScheduleIndex(spec)
    index.record(ITEM, f"/a/{SCHEDULES_FILE}")

    async def _ls(item_id: str) -> list[str]:
        return [f"/b/{SCHEDULES_FILE}"]

    await reconcile_item_schedules(ITEM, ls=_ls, index=index)
    await reconcile_item_schedules(ITEM, ls=_ls, index=index)

    assert index.paths(ITEM) == [f"/a/{SCHEDULES_FILE}", f"/b/{SCHEDULES_FILE}"]


async def test_a_listing_that_fails_does_not_fail_the_turn(spec: SpecStar) -> None:
    """This runs at turn end, after the user's work is done and persisted.

    Raising here would turn "the schedule index is a little behind" into "the
    turn reports an error", and the sweep already recovers on its own the next
    time anything writes. The loud failure would cost more than the quiet lag.
    """
    index = ScheduleIndex(spec)

    async def _ls(item_id: str) -> list[str]:
        raise RuntimeError("the sandbox went away mid-listing")

    await reconcile_item_schedules(ITEM, ls=_ls, index=index)  # must not raise

    assert index.paths(ITEM) == []


# --- the turn-end hook itself, driven ----------------------------------------


async def test_the_turn_end_hook_actually_calls_the_reconcile() -> None:
    """Wired is not the same as called.

    The source guard next door checks `create_app` passes a reconcile into
    `_reconcile_after_turn`. It cannot see whether the hook then invokes it —
    turning the call into `if False:` left that guard green, which is the same
    token-not-behaviour hole P45 removed from this file's sibling. So the hook
    is driven here.

    Order matters: the reconcile lists the workspace, so it has to run AFTER the
    flush has settled the bytes, or it lists a workspace the turn has not
    finished writing to.
    """
    calls: list[str] = []

    async def _flush(item_id: str) -> None:
        calls.append("flush")

    def _forget(item_id: str) -> None:
        calls.append("forget")

    async def _reconcile(item_id: str) -> None:
        calls.append("reconcile")

    hook = _reconcile_after_turn(_flush, _forget, _reconcile)
    await hook(ITEM)

    assert calls == ["flush", "forget", "reconcile"], (
        f"the turn-end hook did {calls}; the reconcile must run, and must run "
        "after the flush that settles the bytes it will list"
    )


async def test_a_failed_flush_still_drops_the_size_it_knows_is_wrong() -> None:
    """The control, and the reason `forget` sits in a `finally`.

    A hook that ran its steps in a plain sequence would satisfy the test above
    and silently keep a measurement it already knows is stale.
    """
    calls: list[str] = []

    async def _flush(item_id: str) -> None:
        raise RuntimeError("the sandbox went away")

    def _forget(item_id: str) -> None:
        calls.append("forget")

    async def _reconcile(item_id: str) -> None:
        calls.append("reconcile")

    with pytest.raises(RuntimeError):
        await _reconcile_after_turn(_flush, _forget, _reconcile)(ITEM)

    assert calls == ["forget"], (
        "a failed flush must still drop the cached size, and must NOT go on to "
        "list a workspace whose bytes never settled"
    )
