"""P1 of `docs/plan-wui-usage.md`: a deployed page's opens and uses, counted in
memory, written back as one row per page per day per process, read back as
person-days (U2) and as counts and total time (U3).

The store is the real one over an in-memory spec; days are pinned.
"""

from __future__ import annotations

import pytest

from workspace_app.api.wui_usage import (
    WuiUsageDay,
    WuiUsageRecorder,
    WuiUsageStore,
    register_wui_usage,
    row_id,
)
from workspace_app.resources import make_spec

TODAY = "2026-11-04"
PAGE = ("item-1", "/report/page.ai.yaml")


def _spec():
    spec = make_spec()
    register_wui_usage(spec)
    return spec


def _store(spec, *, today: str = TODAY):
    """A fresh recorder + store: one process. Two calls are two processes."""
    recorder = WuiUsageRecorder(today=lambda: today)
    return recorder, WuiUsageStore(spec, recorder, today=lambda: today)


def test_one_person_opening_a_page_many_times_in_a_day_is_one_person_day():
    spec = _spec()
    rec, store = _store(spec)
    for _ in range(3):
        rec.opened(*PAGE, user="alice")
    rec.opened(*PAGE, user="bob")
    store.flush()

    got = store.summary([PAGE])[PAGE]

    assert (got.opened_total, got.opened_7d) == (2, 2)


def test_the_same_person_counted_by_two_processes_is_still_one_person_day():
    """Each process writes its own row; a person-day is the people of a day
    across them all, not the sum of each process's count."""
    spec = _spec()
    rec_a, store_a = _store(spec)
    rec_b, store_b = _store(spec)
    rec_a.opened(*PAGE, user="alice")
    rec_b.opened(*PAGE, user="alice")
    rec_b.opened(*PAGE, user="carol")
    store_a.flush()
    store_b.flush()

    assert store_a.summary([PAGE])[PAGE].opened_total == 2


def test_the_last_seven_days_are_today_and_the_six_before_it_and_the_total_is_every_day():
    spec = _spec()
    # Today and the six days before it are 10-29..11-04: 10-28 is just outside.
    for day in ("2026-10-20", "2026-10-28", "2026-10-29", TODAY):
        rec, store = _store(spec, today=day)
        rec.opened(*PAGE, user="alice")
        store.flush()
    _rec, store = _store(spec)

    got = store.summary([PAGE])[PAGE]

    assert got.opened_total == 4  # every day kept (U9)
    assert got.opened_7d == 2


def test_uses_are_counted_and_their_time_added_up_across_the_three_kinds():
    """U3/U4: a count and a total time, whatever the kind."""
    spec = _spec()
    rec, store = _store(spec)
    rec.used(*PAGE, user="alice", kind="tool", ms=1000)
    rec.used(*PAGE, user="alice", kind="tool", ms=500)
    rec.used(*PAGE, user="bob", kind="workflow", ms=60_000)
    rec.used(*PAGE, user="bob", kind="agent", ms=2_500)
    store.flush()

    got = store.summary([PAGE])[PAGE]

    assert (got.used_total, got.used_ms_total) == (4, 64_000)
    assert (got.used_7d, got.used_ms_7d) == (4, 64_000)
    # Using a page is not opening it: no person-day from uses alone.
    assert got.opened_total == 0


def test_an_unknown_kind_is_refused():
    rec = WuiUsageRecorder(today=lambda: TODAY)
    with pytest.raises(ValueError):
        rec.used(*PAGE, user="alice", kind="build", ms=1)


def test_a_page_nobody_used_reads_as_zeros():
    spec = _spec()
    _rec, store = _store(spec)

    got = store.summary([PAGE])[PAGE]

    assert (got.opened_total, got.opened_7d, got.used_total, got.used_ms_total) == (0, 0, 0, 0)


def test_a_flush_adds_to_the_row_and_keeps_one_revision():
    spec = _spec()
    rec, store = _store(spec)
    rec.opened(*PAGE, user="alice")
    store.flush()
    rec.used(*PAGE, user="alice", kind="tool", ms=10)
    store.flush()
    rec.used(*PAGE, user="alice", kind="tool", ms=20)
    store.flush()

    rid = row_id(PAGE[0], PAGE[1], TODAY)
    rm = spec.get_resource_manager(WuiUsageDay)
    assert len(list(rm.list_revisions(rid))) == 1
    got = store.summary([PAGE])[PAGE]
    assert (got.opened_total, got.used_total, got.used_ms_total) == (1, 2, 30)


def test_counts_whose_write_failed_are_kept_for_the_next_flush(monkeypatch):
    spec = _spec()
    rec, store = _store(spec)
    rec.opened(*PAGE, user="alice")
    real = WuiUsageStore._add
    calls = {"n": 0}

    def fail_once(self, *args, **kwargs):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("db down")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(WuiUsageStore, "_add", fail_once)

    with pytest.raises(OSError):
        store.flush()
    store.flush()

    assert store.summary([PAGE])[PAGE].opened_total == 1


def test_summary_reads_only_the_pages_asked_for():
    spec = _spec()
    rec, store = _store(spec)
    other = ("item-2", "/x/page.ai.yaml")
    rec.opened(*PAGE, user="alice")
    rec.opened(*other, user="bob")
    rec.opened("item-1", "/other/page.ai.yaml", user="carol")
    store.flush()

    got = store.summary([PAGE])

    assert list(got) == [PAGE]
    assert got[PAGE].opened_total == 1


def test_the_app_writes_what_it_counted_when_it_stops():
    """U9: counts still in memory at shutdown are written, not lost."""
    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    with TestClient(app):
        app.state.wui_usage_store.recorder.opened(*PAGE, user="alice")

    rows = [r.data for r in spec.get_resource_manager(WuiUsageDay).list_resources()]
    assert all(isinstance(r, WuiUsageDay) for r in rows)
    assert [r.by_user["alice"][0] for r in rows if isinstance(r, WuiUsageDay)] == [1]


# ─── review round 1 (defect 4 / regression 2): bounded reads ─────────────────


def test_a_page_has_one_row_a_day_whatever_process_counted_it():
    """U9 as the person chose it: a row per page per day. Two processes (or a
    restart, which is a new process) merge into it rather than adding rows."""
    spec = _spec()
    rec_a, store_a = _store(spec)
    rec_b, store_b = _store(spec)
    rec_a.opened(*PAGE, user="alice")
    rec_b.opened(*PAGE, user="bob")
    rec_b.used(*PAGE, user="bob", kind="tool", ms=10)
    store_a.flush()
    store_b.flush()

    rows = [r.data for r in spec.get_resource_manager(WuiUsageDay).list_resources()]

    assert len(rows) == 1
    assert sorted(rows[0].by_user) == ["alice", "bob"]


def test_the_total_is_kept_apart_so_the_card_never_reads_every_day():
    """The card's 「總共」 comes from one row per page, kept up to date as days
    are written — drop every day row older than the week and it still holds."""
    spec = _spec()
    days = ("2026-09-01", "2026-09-02", "2026-11-03", TODAY)
    for day in days:
        for _process, user in (("pod-a", "alice"), ("pod-b", "alice"), ("pod-b", "bob")):
            rec, store = _store(spec, today=day)
            rec.opened(*PAGE, user=user)
            rec.used(*PAGE, user=user, kind="agent", ms=100)
            store.flush()
    rm = spec.get_resource_manager(WuiUsageDay)
    for res in list(rm.list_resources()):
        if res.data.day < "2026-10-29":
            rm.permanently_delete(res.info.resource_id)
    _rec, store = _store(spec)

    got = store.summary([PAGE])[PAGE]

    assert got.opened_total == 8  # alice and bob, on four days — alice's two processes once
    assert (got.used_total, got.used_ms_total) == (12, 1200)
    assert (got.opened_7d, got.used_7d) == (4, 6)


def test_a_write_that_races_another_process_is_tried_again():
    """Two processes merge into one row: a write against a row someone else
    changed since it was read is refused (etag) and redone, not lost."""
    from specstar.types import PreconditionFailedError

    spec = _spec()
    rec, store = _store(spec)
    rec.opened(*PAGE, user="alice")
    store.flush()
    rm = spec.get_resource_manager(WuiUsageDay)
    real = type(rm).update
    raced = {"n": 0}

    def once_stale(self, *args, **kwargs):  # noqa: ANN001, ANN202
        if self is rm and kwargs.get("expected_etag") is not None and raced["n"] == 0:
            raced["n"] += 1
            raise PreconditionFailedError(args[0], "old", "new")
        return real(self, *args, **kwargs)

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(rm), "update", once_stale)
        rec.opened(*PAGE, user="bob")
        store.flush()

    assert raced["n"] == 1
    usage = store.summary([PAGE])[PAGE]
    assert (usage.opened_total, usage.opened_7d) == (2, 2)  # the total row, then the day row


def test_a_total_that_races_another_process_is_tried_again():
    """The total row is merged the same way: a refused write is redone."""
    from specstar.types import PreconditionFailedError

    from workspace_app.api.wui_usage import WuiUsageTotal

    spec = _spec()
    rec, store = _store(spec)
    rec.opened(*PAGE, user="alice")
    store.flush()
    rm = spec.get_resource_manager(WuiUsageTotal)
    real = type(rm).update
    raced = {"n": 0}

    def once_stale(self, *args, **kwargs):  # noqa: ANN001, ANN202
        if self is rm and kwargs.get("expected_etag") is not None and raced["n"] == 0:
            raced["n"] += 1
            raise PreconditionFailedError(args[0], "old", "new")
        return real(self, *args, **kwargs)

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(rm), "update", once_stale)
        rec.opened(*PAGE, user="bob")
        store.flush()

    assert raced["n"] == 1
    assert store.summary([PAGE])[PAGE].opened_total == 2
