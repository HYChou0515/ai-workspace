"""The reconcile pass that keeps the disk ledger complete
(docs/plan-storage-all-items.md decision 5): every item a person owns gets a
row, with its durable size when no sandbox is live for it; live items are left
to the mirror; rows of items that no longer exist go.

Against the real ledger (`make_spec`); liveness, durable size and existence are
handed in, as the job hands them in.
"""

from __future__ import annotations

import pytest

from workspace_app.quota.disk_ledger import DiskLedger, register_disk_ledger
from workspace_app.quota.disk_reconcile import reconcile_disk_ledger
from workspace_app.resources import make_spec


def _ledger(now: list[int]) -> DiskLedger:
    spec = make_spec()
    register_disk_ledger(spec)
    return DiskLedger(spec, now_ms=lambda: now[0])


async def _run(ledger, items, *, live=(), sizes=None, gone=(), broken=()):
    sizes = sizes or {}

    async def usage(item_id: str) -> int:
        if item_id in broken:
            raise OSError(f"cannot read {item_id}")
        return sizes[item_id]

    async def is_live(item_id: str) -> bool:
        return item_id in live

    return await reconcile_disk_ledger(
        items=items,
        is_live=is_live,
        usage=usage,
        exists=lambda item_id: item_id not in gone,
        ledger=ledger,
    )


async def test_an_item_never_measured_gets_its_durable_size():
    """The headline: an item untouched since the ledger shipped had no row, so
    it was not listed and counted 0."""
    ledger = _ledger([1])
    report = await _run(ledger, [("old", "alice")], sizes={"old": 4096})
    assert await ledger.per_item_for("alice") == [("old", 4096)]
    assert report.recorded == 1


async def test_a_reaped_items_frozen_size_is_refreshed():
    ledger = _ledger([1])
    await ledger.record("reaped", "alice", 9000)  # measured while live
    await _run(ledger, [("reaped", "alice")], sizes={"reaped": 3000})
    assert await ledger.per_item_for("alice") == [("reaped", 3000)]


async def test_a_live_item_is_left_to_the_mirror():
    """Its durable copy is additive while it is live (#538): the mirror's
    measurement is the honest one."""
    ledger = _ledger([1])
    await ledger.record("busy", "alice", 500)
    report = await _run(
        ledger,
        [("busy", "alice"), ("new", "alice")],
        live={"busy", "new"},
        sizes={"busy": 9, "new": 9},
    )
    assert await ledger.per_item_for("alice") == [("busy", 500)]
    assert report.skipped_live == 2


async def test_a_moved_item_is_charged_to_its_new_owner():
    ledger = _ledger([1])
    await ledger.record("cold", "alice", 100)
    await ledger.record("busy", "alice", 500)
    await _run(ledger, [("cold", "bob"), ("busy", "bob")], live={"busy"}, sizes={"cold": 100})
    assert await ledger.per_item_for("alice") == []
    # A live item keeps its measured size — only the debtor moves.
    assert sorted(await ledger.per_item_for("bob")) == [("busy", 500), ("cold", 100)]


async def test_the_row_of_an_item_that_no_longer_exists_goes():
    ledger = _ledger([1])
    await ledger.record("ghost", "alice", 700)
    await ledger.record("kept", "alice", 1)
    report = await _run(ledger, [("kept", "alice")], sizes={"kept": 1}, gone={"ghost"})
    assert await ledger.per_item_for("alice") == [("kept", 1)]
    assert report.forgotten == 1


async def test_a_row_the_listing_missed_is_not_dropped_while_its_item_exists():
    """The listing is not the proof of absence — an item of an App this process
    did not register would look missing. Only `exists` says gone."""
    ledger = _ledger([1])
    await ledger.record("elsewhere", "alice", 700)
    await _run(ledger, [], sizes={})
    assert await ledger.per_item_for("alice") == [("elsewhere", 700)]


async def test_one_unreadable_item_costs_only_its_own_row():
    ledger = _ledger([1])
    report = await _run(
        ledger, [("bad", "alice"), ("good", "alice")], sizes={"good": 10}, broken={"bad"}
    )
    assert await ledger.per_item_for("alice") == [("good", 10)]
    assert report.failed == 1


async def test_an_up_to_date_row_is_not_rewritten():
    now = [1]
    ledger = _ledger(now)
    await ledger.record("same", "alice", 42)
    now[0] = 2
    report = await _run(ledger, [("same", "alice")], sizes={"same": 42})
    assert report.recorded == 0
    rows = await ledger.rows()
    assert rows["same"].measured_at_ms == 1


@pytest.mark.parametrize("owner", ["", "   "])
async def test_an_item_nobody_owes_for_is_not_booked_to_nobody(owner: str):
    ledger = _ledger([1])
    await _run(ledger, [("orphan", owner)], sizes={"orphan": 5})
    assert await ledger.rows() == {}
