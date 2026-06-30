"""#355 daily auto-sync: CodeRepoSweeper.tick() enqueues a ``code_sync`` job for
every code Collection due for its server-local daily wall-clock time
(``daily_sync`` = ``HH:MM``), replacing the old per-collection
``sync_interval_hours`` interval.

The sweeper is a pure producer — it does NOT clone (the enqueued job does, on
the wiki worker). Tests inject a fake ``enqueue`` to observe which collections it
fires for, and call ``tick(now_ms=…)`` directly. ``now_ms`` is built from a
*local* ``datetime`` so the gate's local-time maths round-trips identically
regardless of the machine's timezone.
"""

from __future__ import annotations

from datetime import datetime

import msgspec
from specstar import SpecStar

from workspace_app.kb.code_repo import (
    CodeRepoSweeper,
    _due_for_daily_sync,
    parse_daily_sync,
)
from workspace_app.resources.kb import Collection


def _ms(dt: datetime) -> int:
    """A local-datetime → epoch-ms helper, so tests are timezone-agnostic."""
    return int(dt.timestamp() * 1000)


def _sweeper(spec: SpecStar, enqueue, *, daily_sync: str | None = "03:00") -> CodeRepoSweeper:
    return CodeRepoSweeper(spec, enqueue=enqueue, daily_sync=daily_sync)


def _code_collection(spec: SpecStar) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name="repo", git_url="https://git.example/r.git", embedder_id=1))
        .resource_id
    )


# ── pure gate helper ──────────────────────────────────────────────────────


def test_parse_daily_sync_valid_and_invalid():
    assert parse_daily_sync("03:00") == (3, 0)
    assert parse_daily_sync("23:59") == (23, 59)
    assert parse_daily_sync(None) is None
    assert parse_daily_sync("") is None
    assert parse_daily_sync("3am") is None  # not HH:MM
    assert parse_daily_sync("24:00") is None  # hour out of range
    assert parse_daily_sync("03:60") is None  # minute out of range
    assert parse_daily_sync("a:b") is None  # non-numeric


def test_due_gate_fires_once_past_the_daily_time():
    target = datetime(2026, 1, 2, 3, 0)
    before = _ms(datetime(2026, 1, 2, 2, 30))  # before today's 03:00
    at = _ms(datetime(2026, 1, 2, 3, 5))  # just after 03:00
    # Never synced, past today's time → due.
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=None, daily_sync="03:00") is True
    # Last pull was before today's time → due.
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=before, daily_sync="03:00") is True
    # Already synced after today's time → not due again today.
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=_ms(target), daily_sync="03:00") is False


def test_due_gate_not_before_the_daily_time():
    early = _ms(datetime(2026, 1, 2, 2, 0))  # before 03:00
    assert _due_for_daily_sync(now_ms=early, last_pulled_ms=None, daily_sync="03:00") is False


def test_due_gate_off_when_daily_sync_unset():
    at = _ms(datetime(2026, 1, 2, 12, 0))
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=None, daily_sync=None) is False
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=None, daily_sync="") is False


# ── tick: due → enqueue ────────────────────────────────────────────────────


def test_tick_enqueues_code_collection_due_today(spec: SpecStar):
    """A code collection never synced, past today's daily time, is enqueued."""
    cid = _code_collection(spec)
    enqueued: list[str] = []
    sweeper = _sweeper(spec, enqueued.append, daily_sync="03:00")
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 3, 5))) == [cid]
    assert enqueued == [cid]


def test_tick_skips_before_daily_time(spec: SpecStar):
    _code_collection(spec)
    enqueued: list[str] = []
    sweeper = _sweeper(spec, enqueued.append, daily_sync="03:00")
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 1, 0))) == []
    assert enqueued == []


def test_tick_resyncs_next_day(spec: SpecStar):
    """A collection pulled yesterday is due again past today's daily time."""
    rm = spec.get_resource_manager(Collection)
    cid = _code_collection(spec)
    coll = rm.get(cid).data
    rm.update(
        cid, msgspec.structs.replace(coll, git_last_pulled_at=_ms(datetime(2026, 1, 1, 3, 0)))
    )
    enqueued: list[str] = []
    sweeper = _sweeper(spec, enqueued.append, daily_sync="03:00")
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 3, 5))) == [cid]


def test_tick_off_when_daily_sync_none(spec: SpecStar):
    """daily_sync=None ⇒ the sweeper never enqueues (manual /sync only)."""
    _code_collection(spec)
    enqueued: list[str] = []
    sweeper = _sweeper(spec, enqueued.append, daily_sync=None)
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 12, 0))) == []
    assert enqueued == []


def test_tick_skips_non_code_collections(spec: SpecStar):
    """A plain (no git_url) collection is ignored entirely."""
    spec.get_resource_manager(Collection).create(Collection(name="plain", embedder_id=1))
    enqueued: list[str] = []
    sweeper = _sweeper(spec, enqueued.append, daily_sync="03:00")
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 4, 0))) == []
    assert enqueued == []
