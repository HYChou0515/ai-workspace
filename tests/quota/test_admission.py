"""P5 — per-person admission control for NEW sandboxes.

The plan's acceptance conditions, in order:

1. over `count` ⇒ a new item's turn is refused BEFORE the user's message is
   persisted (that one lives in `test_turn_gate.py`, at the service boundary)
2. an item that already has a live sandbox is never refused
3. killing a sandbox gives the slot back with NO decrement anywhere
4. a pod dying without reaping ALSO gives the slot back — the tally is derived
   from a liveness window, not from a counter somebody has to maintain
"""

from __future__ import annotations

import pytest

from workspace_app.api.sandbox_activity import (
    IActivityStore,
    LiveSandbox,
    SpecstarActivityStore,
    register_sandbox_activity,
)
from workspace_app.config.schema import PerUserResources
from workspace_app.quota.admission import AdmissionGate, SandboxQuotaExceeded
from workspace_app.quota.limits import ResourceLimits
from workspace_app.resources import make_spec

ONE_CORE = ResourceLimits(cpu_cores=1.0, memory_bytes=512 * 1024**2, disk_bytes=0)


class _Clock:
    def __init__(self) -> None:
        self.ms = 1_000_000

    def __call__(self) -> int:
        return self.ms


def _store() -> tuple[SpecstarActivityStore, _Clock]:
    spec = make_spec()
    register_sandbox_activity(spec)
    clock = _Clock()
    return SpecstarActivityStore(spec, now_ms=clock), clock


def _gate(
    store: IActivityStore | None,
    clock: _Clock,
    limits: PerUserResources,
) -> AdmissionGate:
    async def _limits_for(_owner: str) -> PerUserResources:
        return limits

    return AdmissionGate(
        store,
        _limits_for,
        owner_of=lambda item: "alice" if item.startswith("a-") else None,
        # These tests weigh the incoming sandbox EXPLICITLY (`check(item, limits)`)
        # to isolate the arithmetic, so there is nothing for the gate to ask.
        # Stated rather than defaulted: the production wiring is what proves the
        # default path, and an omission here used to be indistinguishable from it.
        cost_of=None,
        window_ms=1800_000,  # the reaper's idle threshold
        now_ms=clock,
    )


# ─── the count dimension ───────────────────────────────────────────────


async def test_refuses_the_one_that_would_exceed_the_count():
    store, clock = _store()
    await store.bump("a-1", owner="alice", cpu_milli=1000)
    await store.bump("a-2", owner="alice", cpu_milli=1000)
    gate = _gate(store, clock, PerUserResources(count=2))
    with pytest.raises(SandboxQuotaExceeded) as err:
        await gate.check("a-3", ONE_CORE)
    assert err.value.dimension == "sandboxes"
    assert err.value.owner == "alice"


async def test_exactly_reaching_the_limit_is_allowed():
    """The boundary is the same one the disk quota uses: reaching it is fine,
    exceeding it is not."""
    store, clock = _store()
    await store.bump("a-1", owner="alice", cpu_milli=1000)
    await _gate(store, clock, PerUserResources(count=2)).check("a-2", ONE_CORE)


async def test_another_persons_sandboxes_do_not_count():
    store, clock = _store()
    for i in range(5):
        await store.bump(f"b-{i}", owner="bob", cpu_milli=1000)
    await _gate(store, clock, PerUserResources(count=2)).check("a-1", ONE_CORE)


# ─── never refuse what is already open ─────────────────────────────────


async def test_an_item_that_already_has_a_sandbox_is_never_refused():
    """Otherwise someone at their limit cannot use the environments they already
    have open — which is the opposite of what the limit is for.

    "Already has one" is a HEARTBEAT, which is also the row the tally counts.
    It used to be a separate probe injected into the gate, and on `kind: local`
    that probe answered "does the item's directory exist" — permanently true
    after the item's first run, so an item could close its environment and let
    itself straight back in past a full quota. One source, one answer.
    """
    store, clock = _store()
    for i in range(9):
        await store.bump(f"a-{i}", owner="alice", cpu_milli=1000)
    await store.bump("a-warm", owner="alice", cpu_milli=1000)

    await _gate(store, clock, PerUserResources(count=2)).check("a-warm", ONE_CORE)  # no raise


# ─── self-healing: the two conditions from the plan ────────────────────


async def test_killing_a_sandbox_returns_the_slot_with_no_decrement():
    store, clock = _store()
    await store.bump("a-1", owner="alice", cpu_milli=1000)
    await store.bump("a-2", owner="alice", cpu_milli=1000)
    gate = _gate(store, clock, PerUserResources(count=2))
    with pytest.raises(SandboxQuotaExceeded):
        await gate.check("a-3", ONE_CORE)

    # The reaper's ONLY act is forgetting the row. Nothing subtracts anything.
    await store.forget("a-2")
    await gate.check("a-3", ONE_CORE)  # no raise


async def test_a_pod_dying_without_reaping_also_returns_the_slot():
    """The condition a counter cannot satisfy: nobody called `forget`, nobody
    called `kill`, the process simply vanished. The heartbeat ages out of the
    window and the slot comes back on its own."""
    store, clock = _store()
    await store.bump("a-1", owner="alice", cpu_milli=1000)
    await store.bump("a-2", owner="alice", cpu_milli=1000)
    gate = _gate(store, clock, PerUserResources(count=2))
    with pytest.raises(SandboxQuotaExceeded):
        await gate.check("a-3", ONE_CORE)

    clock.ms += 1800_001  # past the idle window; no cleanup ran at all
    await gate.check("a-3", ONE_CORE)  # no raise


# ─── the cpu / memory dimensions ───────────────────────────────────────


async def test_cpu_counts_the_incoming_sandboxs_own_size():
    """ "Does one more fit?" depends on how big the new one is — 3 of 4 cores
    live admits a 1-core sandbox and refuses a 2-core one."""
    store, clock = _store()
    for i in range(3):
        await store.bump(f"a-{i}", owner="alice", cpu_milli=1000)
    gate = _gate(store, clock, PerUserResources(cpu=4))
    await gate.check("a-new", ResourceLimits(cpu_cores=1.0, memory_bytes=0, disk_bytes=0))
    with pytest.raises(SandboxQuotaExceeded) as err:
        await gate.check("a-new", ResourceLimits(cpu_cores=2.0, memory_bytes=0, disk_bytes=0))
    assert err.value.dimension == "cpu"


async def test_memory_is_summed_over_live_sandboxes():
    store, clock = _store()
    await store.bump("a-1", owner="alice", memory_bytes=512 * 1024**2)
    gate = _gate(store, clock, PerUserResources(memory="1G"))
    with pytest.raises(SandboxQuotaExceeded) as err:
        await gate.check(
            "a-2", ResourceLimits(cpu_cores=0, memory_bytes=600 * 1024**2, disk_bytes=0)
        )
    assert err.value.dimension == "memory"


# ─── the off switches ──────────────────────────────────────────────────


async def test_no_configured_limit_never_refuses():
    store, clock = _store()
    for i in range(50):
        await store.bump(f"a-{i}", owner="alice", cpu_milli=4000)
    await _gate(store, clock, PerUserResources()).check("a-new", ONE_CORE)


class _CountingStore(IActivityStore):
    """Wraps a store and counts the ledger reads. Round-12 M36/M37: the two
    tests below were named after guards they did not pin — with either early
    return deleted they still passed, because `_enforce`'s own `if limit and …`
    refuses nothing when the limits are zero, and a stub `limits_for` that
    ignores its argument answers happily for an owner of `None`.

    What the guards actually buy is the QUERY that follows them, so that is what
    is asserted. "A no-op" in the docstring means no work, not merely no
    refusal — and no work is the observable half."""

    def __init__(self, inner: IActivityStore) -> None:
        self._inner = inner
        self.reads = 0

    async def live_for(self, owner: str, *, since_ms: int) -> list[LiveSandbox]:
        self.reads += 1
        return await self._inner.live_for(owner, since_ms=since_ms)

    async def is_live(self, item_id: str, *, since_ms: int) -> bool:
        self.reads += 1
        return await self._inner.is_live(item_id, since_ms=since_ms)

    # The rest of the contract, delegated explicitly. `__getattr__` would have
    # been shorter and would NOT have satisfied the ABC — which `ty` said and no
    # test would have: a double that implements less than the real thing is
    # immune to exactly the changes worth catching.
    async def bump(
        self,
        item_id: str,
        *,
        owner: str = "",
        cpu_milli: int = 0,
        memory_bytes: int = 0,
    ) -> None:
        await self._inner.bump(item_id, owner=owner, cpu_milli=cpu_milli, memory_bytes=memory_bytes)

    async def last_active_ms(self, item_id: str) -> int | None:
        return await self._inner.last_active_ms(item_id)

    async def forget(self, item_id: str) -> None:
        await self._inner.forget(item_id)

    async def owner_of(self, item_id: str) -> str | None:
        return await self._inner.owner_of(item_id)


async def test_an_unconfigured_limit_does_not_even_ask_the_ledger():
    """M37. Deleting `if not self._configured(limits): return` leaves the older
    assertion green; it does not leave this one green."""
    store, clock = _store()
    for i in range(50):
        await store.bump(f"a-{i}", owner="alice", cpu_milli=4000)
    counting = _CountingStore(store)

    await _gate(counting, clock, PerUserResources()).check("a-new", ONE_CORE)

    assert counting.reads == 0, "queried the ledger for a deploy with no per-person limit"


async def test_an_item_with_no_debtor_does_not_even_ask_the_ledger():
    """M36. Same shape for `if not owner: return` — and the same reason it was
    invisible: with no debtor the tally comes back empty, so nothing is refused
    either way."""
    store, clock = _store()
    await store.bump("a-1", owner="alice", cpu_milli=4000)
    counting = _CountingStore(store)

    # `owner_of` answers None for ids outside the "a-" prefix in this harness.
    await _gate(counting, clock, PerUserResources(count=1)).check("orphan-1", ONE_CORE)

    assert counting.reads == 0, "queried the ledger for an item with no resolvable debtor"


async def test_single_process_deploy_without_a_shared_store_is_not_gated():
    """No shared activity store ⇒ no cross-pod tally to enforce against. Gating
    on a pod-local guess would refuse people for sandboxes this pod cannot see."""
    _store_unused, clock = _store()
    await _gate(None, clock, PerUserResources(count=1)).check("a-1", ONE_CORE)


async def test_an_item_with_no_resolvable_owner_is_not_gated():
    """Charging a limit to nobody can only produce a refusal no person can act
    on. (`owner_of` returns None for ids outside the "a-" prefix here.)"""
    store, clock = _store()
    await _gate(store, clock, PerUserResources(count=1)).check("orphan-1", ONE_CORE)


async def test_live_for_is_scoped_to_the_owner_and_the_window():
    store, clock = _store()
    await store.bump("a-1", owner="alice", cpu_milli=1500, memory_bytes=99)
    await store.bump("b-1", owner="bob", cpu_milli=1000)
    got = await store.live_for("alice", since_ms=clock.ms - 1000)
    assert got == [LiveSandbox(item_id="a-1", cpu_milli=1500, memory_bytes=99)]
    assert await store.live_for("alice", since_ms=clock.ms + 1) == []
