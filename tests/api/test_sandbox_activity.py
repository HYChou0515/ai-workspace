"""#345: the shared per-item activity heartbeat over specstar."""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.api.sandbox_activity import (
    SpecstarActivityStore,
    register_sandbox_activity,
)


def _store() -> tuple[SpecstarActivityStore, dict[str, int]]:
    spec = SpecStar()
    register_sandbox_activity(spec)
    register_sandbox_activity(spec)  # idempotent — safe on every pod
    clock = {"t": 1000}
    return SpecstarActivityStore(spec, now_ms=lambda: clock["t"]), clock


async def test_bump_read_upsert_and_forget():
    store, clock = _store()
    assert await store.last_active_ms("ws-1") is None  # unknown → None

    await store.bump("ws-1")
    assert await store.last_active_ms("ws-1") == 1000

    clock["t"] = 2000
    await store.bump("ws-1")  # upsert the existing row
    assert await store.last_active_ms("ws-1") == 2000

    await store.forget("ws-1")
    assert await store.last_active_ms("ws-1") is None
    await store.forget("ws-1")  # idempotent — no error when already gone


async def test_distinct_items_are_independent():
    store, _clock = _store()
    await store.bump("a")
    await store.bump("b")
    assert await store.last_active_ms("a") == 1000
    assert await store.last_active_ms("b") == 1000
    await store.forget("a")
    assert await store.last_active_ms("a") is None
    assert await store.last_active_ms("b") == 1000


async def test_default_clock_stamps_a_real_timestamp():
    # No injected clock ⇒ the real wall-clock branch; the value is just a
    # positive epoch-ms (exercises the default-clock path).
    spec = SpecStar()
    register_sandbox_activity(spec)
    store = SpecstarActivityStore(spec)  # now_ms=None → real clock
    await store.bump("ws-1")
    ms = await store.last_active_ms("ws-1")
    assert ms is not None and ms > 0


async def test_bump_after_forget_restores_the_row():
    # #345: a forgotten (soft-deleted) item that becomes active again must
    # restore + re-stamp, not error — the ResourceIsDeletedError branch.
    store, clock = _store()
    await store.bump("ws-1")
    await store.forget("ws-1")  # soft-delete
    assert await store.last_active_ms("ws-1") is None
    clock["t"] = 5000
    await store.bump("ws-1")  # hits IsDeleted → restore + modify
    assert await store.last_active_ms("ws-1") == 5000


async def test_a_heartbeat_that_does_not_know_the_owner_keeps_the_one_on_record():
    """Round-6 finding 1, the backstop half.

    `""` on the way in means "I could not resolve the debtor", NEVER "nobody
    owes for this". The two were the same value, so an item the seam could not
    resolve made its next heartbeat ERASE the owner from a row that already
    named somebody — and a running sandbox charged to nobody is one the
    admission gate skips, the tally omits, and `/me/resources` cannot show to
    the person who would close it.

    The seam is fixed to resolve soft-deleted items rather than report them
    absent, so this path should now be unreachable. It is guarded anyway,
    because the cost of being wrong is silent unbilled capacity and the guard
    is one comparison. A row's debtor is only ever REPLACED by another name.
    """
    store, _clock = _store()

    await store.bump("ws-1", owner="alice", cpu_milli=8000, memory_bytes=1 << 33)
    await store.bump("ws-1")

    assert await store.owner_of("ws-1") == "alice"
    assert [s.item_id for s in await store.live_for("alice", since_ms=0)] == ["ws-1"]
