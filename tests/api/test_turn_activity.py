"""A turn's cross-pod liveness heartbeat.

The screen cannot tell "running on another pod, producing nothing yet" from
"nobody is working on this": the live stream is per-pod, so hearing nothing
proves nothing, and the persisted thread is written at turn END, so mid-turn it
says exactly what an abandoned turn says. Every notice built on that ambiguity
had to guess — and each guess this branch tried was wrong in one direction or
the other.

This is the missing fact, in the shape the sandbox reaper already uses for the
same class of question (`sandbox_activity`): a heartbeat row in the shared store,
which any pod can read, and which ages out on its own when the pod writing it
dies. That last part is the point — nothing has to notice a crash for the answer
to become "no".
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.api.turn_activity import (
    SpecstarTurnActivityStore,
    register_turn_activity,
)


def _store() -> tuple[SpecstarTurnActivityStore, dict[str, int]]:
    spec = SpecStar()
    register_turn_activity(spec)
    register_turn_activity(spec)  # idempotent — every pod calls it at startup
    clock = {"t": 1_000_000}
    store = SpecstarTurnActivityStore(spec, now_ms=lambda: clock["t"])
    store._spec = spec  # noqa: SLF001 — `_delete_row` reaches the same backend
    return store, clock


def _delete_row(store: SpecstarTurnActivityStore, key: str) -> None:
    """Soft-delete the beat row the way the model's auto-generated CRUD would.
    The app itself never does this — liveness expires instead."""
    from workspace_app.api.turn_activity import _TurnActivity

    store._spec.get_resource_manager(_TurnActivity).delete(key)  # noqa: SLF001


async def test_a_turn_is_alive_while_it_keeps_beating():
    store, clock = _store()
    assert await store.alive("chat-1", stale_after_ms=30_000) is False  # never started

    await store.bump("chat-1")
    assert await store.alive("chat-1", stale_after_ms=30_000) is True

    clock["t"] += 20_000  # quiet, but the driver is still beating
    await store.bump("chat-1")
    clock["t"] += 20_000
    assert await store.alive("chat-1", stale_after_ms=30_000) is True


async def test_a_turn_whose_pod_died_stops_being_alive_on_its_own():
    # No cleanup runs, nothing observes the crash: the answer changes because
    # the heartbeat simply stops. A flag that had to be cleared by the dying pod
    # would say "running" forever, which is the state the user is stuck in today.
    store, clock = _store()
    await store.bump("chat-1")

    clock["t"] += 31_000

    assert await store.alive("chat-1", stale_after_ms=30_000) is False


async def test_two_chats_do_not_answer_for_each_other():
    store, clock = _store()
    await store.bump("chat-1")

    assert await store.alive("chat-2", stale_after_ms=30_000) is False

    await store.bump("chat-2")
    clock["t"] += 31_000
    await store.bump("chat-2")
    assert await store.alive("chat-1", stale_after_ms=30_000) is False
    assert await store.alive("chat-2", stale_after_ms=30_000) is True


def _flaky_reads(
    store: SpecstarTurnActivityStore, monkeypatch, misses: int, error=None
) -> list[int]:
    """The disk backend rewrites a draft in place: a read that lands mid-rewrite
    finds no data file (`FileNotFoundError`) or half of one (`ResourceDecodeError`,
    "Input data was truncated" — seen on CI). The first `misses` reads do."""
    from workspace_app.api.turn_activity import _TurnActivity

    rm = store._spec.get_resource_manager(_TurnActivity)  # noqa: SLF001
    real = rm.get
    calls: list[int] = []

    def get(key, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
        calls.append(1)
        if len(calls) <= misses:
            raise error(key) if error else FileNotFoundError(f"…/{key}/data")
        return real(key, *a, **kw)

    monkeypatch.setattr(rm, "get", get)
    return calls


async def test_a_read_caught_mid_rewrite_is_read_again(monkeypatch):
    """`test_two_pods_on_one_store_a_stalled_owner_is_taken_over…` failed about a
    third of the time: the owner's beat rewrote the row while the peer read it,
    the read raised, and that round judged nothing — no takeover. The rewrite
    lasts an instant, so the next read sees the row."""
    store, _clock = _store()
    await store.bump("chat-1")
    calls = _flaky_reads(store, monkeypatch, misses=1)

    assert await store.alive("chat-1", stale_after_ms=30_000) is True
    assert len(calls) == 2


async def test_a_read_of_a_half_written_row_is_read_again(monkeypatch):
    """The same race caught a moment later: the data file is there, half
    written. CI saw it after the FileNotFoundError reread went in."""
    from specstar.types import ResourceDecodeError

    store, _clock = _store()
    await store.bump("chat-1")
    calls = _flaky_reads(
        store,
        monkeypatch,
        misses=1,
        error=lambda key: ResourceDecodeError(key, "Input data was truncated"),
    )

    assert await store.alive("chat-1", stale_after_ms=30_000) is True
    assert len(calls) == 2


async def test_a_row_that_stays_unreadable_is_still_an_error(monkeypatch):
    """Not 'alive forever': a data file that stays missing is not a rewrite in
    progress. After a few reads it raises, as before, and the caller's
    'could not be judged' handles it."""
    import pytest

    store, _clock = _store()
    await store.bump("chat-1")
    calls = _flaky_reads(store, monkeypatch, misses=100)

    with pytest.raises(FileNotFoundError):
        await store.alive("chat-1", stale_after_ms=30_000)
    assert 1 < len(calls) <= 5


async def test_a_beat_brings_back_a_row_someone_deleted():
    # Nothing in the app deletes this row — the turn's liveness expires on its
    # own — but the model carries auto-generated CRUD, so it CAN be deleted from
    # outside. A turn that is still running has to come back on its next beat,
    # or it would read abandoned for the rest of its life.
    store, _clock = _store()
    await store.bump("chat-1")
    _delete_row(store, "chat-1")

    await store.bump("chat-1")

    assert await store.alive("chat-1", stale_after_ms=30_000) is True
