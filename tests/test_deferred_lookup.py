"""#624 §9.12: values that may do I/O must never be computed on the event loop.

Two rungs of the ceiling ladder are read from `_budget_for`, which runs inside
`async def build_chat_turn`, and BOTH turned out to do network I/O:

  - the `/tokenize` probe — a synchronous POST with a 3s timeout;
  - `catalog_limit` — which looks like a table lookup and is not: litellm
    resolves an `ollama/*` name by asking the daemon, with NO timeout at all
    (measured: 129,781 ms against an address that does not answer).

One mechanism serves both, because two would drift.
"""

from __future__ import annotations

import asyncio
import time

from workspace_app.context_budget import deferred_lookup


def test_a_sync_caller_just_computes_it():
    """No event loop, nothing to protect — asking inline is correct, and it is
    what a worker or a test wants."""
    cache: dict[str, int | None] = {}

    assert deferred_lookup(cache, "k", lambda: 42) == 42
    assert cache["k"] == 42


async def test_an_async_caller_is_never_made_to_wait():
    cache: dict[str, int | None] = {}

    started = time.perf_counter()
    got = deferred_lookup(cache, "k", lambda: (time.sleep(0.5), 42)[1])
    elapsed = time.perf_counter() - started

    assert got is None, "not back yet ⇒ unknown, which already means 'send it all'"
    assert elapsed < 0.2, f"the lookup blocked the event loop for {elapsed:.2f}s"


async def test_the_answer_arrives_for_the_next_turn():
    cache: dict[str, int | None] = {}

    deferred_lookup(cache, "k", lambda: 42)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if cache.get("k") is not None:
            break

    assert deferred_lookup(cache, "k", lambda: 99) == 42, "cached, and not recomputed"


async def test_it_is_computed_once_even_while_in_flight():
    """A second turn arriving before the answer must not start a second lookup —
    that is how one slow endpoint becomes a thread per turn."""
    cache: dict[str, int | None] = {}
    calls = 0

    def _slow() -> int:
        nonlocal calls
        calls += 1
        time.sleep(0.2)
        return 42

    for _ in range(5):
        assert deferred_lookup(cache, "k", _slow) is None
    for _ in range(60):
        await asyncio.sleep(0.01)
        if cache.get("k") is not None:
            break

    assert calls == 1


async def test_a_failing_lookup_is_remembered_as_silence():
    """Otherwise every turn retries a broken endpoint forever."""
    cache: dict[str, int | None] = {}
    calls = 0

    def _boom() -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("endpoint down")

    deferred_lookup(cache, "k", _boom)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if "k" in cache:
            break
    deferred_lookup(cache, "k", _boom)
    await asyncio.sleep(0.05)

    assert calls == 1
    assert cache["k"] is None


def test_a_sync_caller_survives_a_failing_lookup():
    """The async path writes a placeholder before scheduling, so the failure
    branch only bites a SYNC caller — and there it is the difference between
    `None` and a KeyError out of a function whose whole contract is "this can
    never break a turn". Mutation testing found it: removing that line left the
    suite green because nothing drove a sync lookup that fails.
    """
    cache: dict[str, int | None] = {}

    def _boom() -> int:
        raise RuntimeError("endpoint down")

    assert deferred_lookup(cache, "k", _boom) is None
    assert cache["k"] is None
