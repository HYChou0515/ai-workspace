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
    return SpecstarTurnActivityStore(spec, now_ms=lambda: clock["t"]), clock


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


async def test_a_finished_turn_is_not_alive():
    store, _clock = _store()
    await store.bump("chat-1")
    await store.finished("chat-1")

    assert await store.alive("chat-1", stale_after_ms=30_000) is False
    await store.finished("chat-1")  # idempotent — a turn can end more than once


async def test_two_chats_do_not_answer_for_each_other():
    store, _clock = _store()
    await store.bump("chat-1")

    assert await store.alive("chat-2", stale_after_ms=30_000) is False

    await store.bump("chat-2")
    await store.finished("chat-1")
    assert await store.alive("chat-1", stale_after_ms=30_000) is False
    assert await store.alive("chat-2", stale_after_ms=30_000) is True


async def test_a_turn_that_starts_again_after_finishing_is_alive_again():
    # `finished` soft-deletes the row; the next turn on the same chat has to
    # bring it back, or a second question would look abandoned from the start.
    store, _clock = _store()
    await store.bump("chat-1")
    await store.finished("chat-1")

    await store.bump("chat-1")

    assert await store.alive("chat-1", stale_after_ms=30_000) is True
