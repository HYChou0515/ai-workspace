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
    *,
    live_items: set[str] | None = None,
) -> AdmissionGate:
    held = live_items or set()

    async def _has_live(item_id: str) -> bool:
        return item_id in held

    return AdmissionGate(
        store,
        limits,
        owner_of=lambda item: "alice" if item.startswith("a-") else None,
        has_live_sandbox=_has_live,
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
    have open — which is the opposite of what the limit is for."""
    store, clock = _store()
    for i in range(9):
        await store.bump(f"a-{i}", owner="alice", cpu_milli=1000)
    gate = _gate(store, clock, PerUserResources(count=2), live_items={"a-warm"})
    await gate.check("a-warm", ONE_CORE)  # no raise


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
