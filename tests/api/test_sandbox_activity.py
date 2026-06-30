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
