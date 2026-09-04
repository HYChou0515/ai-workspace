import tempfile
from pathlib import Path

import pytest

from workspace_app.api.registry import InvestigationRegistry
from workspace_app.api.sandbox_activity import IActivityStore, LiveSandbox
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.migrating import MigratingFileStore
from workspace_app.filestore.nfs_tree import NfsTreeFileStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import (
    RunningSandbox,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)


class _CountingSandbox(MockSandbox):
    """MockSandbox that tracks create/kill call counts for the registry tests."""

    def __init__(self) -> None:
        super().__init__()
        self.create_calls = 0
        self.kill_calls = 0

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.create_calls += 1
        return await super().create(spec, sandbox_id)

    async def kill(self, handle: SandboxHandle) -> None:
        self.kill_calls += 1
        await super().kill(handle)


class _HttpStyleSandbox(MockSandbox):
    """Mimics the http sandbox-host (#366): `create` IGNORES `sandbox_id` and
    mints a fresh unique handle each call, and the backend is NOT id-addressable
    (`handle_for_id` is None). So two pods that each `create` for one item get
    two DIVERGING sandboxes unless they converge via the shared address store."""

    def __init__(self) -> None:
        super().__init__()
        self.create_calls = 0
        self.kill_calls = 0
        # The host records which item each sandbox serves and can be asked. Not
        # modelling that would make every test of the listing vacuous — the
        # double would answer about handles nobody asked about.
        self._item_of: dict[str, str] = {}
        # Handles the LISTING will not name. The real host runs several replicas
        # behind a load balancer, so `GET /sandboxes` answers for the one pod
        # that took the request and no more — put a handle here to place it on
        # another pod. Answering about everything instead made the double
        # STRICTLY more informative than the wire, which silently retired the
        # published address: `_close_unrecorded` reached every sandbox the
        # address existed to reach, so deleting the address branch outright left
        # every test green, including the one named after it.
        self.on_other_replica: set[str] = set()

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.create_calls += 1
        handle = await super().create(spec, sandbox_id=None)  # ignore id → fresh uuid
        if sandbox_id is not None:
            self._item_of[handle.id] = sandbox_id
        return handle

    async def running_sandboxes(self):
        return [
            RunningSandbox(handle=SandboxHandle(id=hid), item_id=self._item_of.get(hid))
            for hid in self._fs
            if hid not in self.on_other_replica
        ]

    def handle_for_id(self, sandbox_id: str) -> SandboxHandle | None:
        return None

    async def kill(self, handle: SandboxHandle) -> None:
        self.kill_calls += 1
        await super().kill(handle)


async def test_session_for_new_workspace_returns_session_with_no_handle():
    registry = InvestigationRegistry(sandbox=MockSandbox())
    session = await registry.session("ws-1")
    assert session.investigation_id == "ws-1"
    assert session.handle is None


async def test_same_investigation_id_returns_same_session_instance():
    registry = InvestigationRegistry(sandbox=MockSandbox())
    a = await registry.session("ws-1")
    b = await registry.session("ws-1")
    assert a is b


async def test_resolve_io_handle_routes_to_shared_dir_then_session_handle_345():
    # #345: file ops route through resolve_io_handle. With a shared per-item dir,
    # even a pod with NO local session must route to the shared dir (id-derived
    # handle) — the facade falls back to the snapshot if it's cold — instead of a
    # stale snapshot. Once this pod warms a session, its handle is used.
    registry = InvestigationRegistry(sandbox=MockSandbox())
    derived = await registry.resolve_io_handle("ws-1")
    assert derived is not None and derived.id == "ws-1"  # no session, still routable
    session = await registry.session("ws-1")
    handle = await registry.ensure_handle(session)
    assert await registry.resolve_io_handle("ws-1") is handle  # session handle once warm


async def test_resolve_io_handle_is_none_when_no_handle_and_no_address_345():
    # An HTTP-style backend mints its own handles (no shared-vol id addressing)
    # and no address store is wired: resolve_io_handle has nothing to derive
    # before a session, so it stays None (¬P) and reads fall back to the snapshot.
    class _NoIdSandbox(MockSandbox):
        def handle_for_id(self, sandbox_id):
            return None

    registry = InvestigationRegistry(sandbox=_NoIdSandbox())
    assert await registry.resolve_io_handle("ws-1") is None


async def test_resolve_io_handle_reads_the_shared_address_on_a_non_owning_pod_492():
    # #492 core: an http item that is warm on pod A must resolve to pod A's ONE
    # sandbox on pod B too — so pod B's writes land in the live sandbox, not a
    # per-pod cold durable write the host's --delete mirror would reconcile away.
    # A non-owning pod has no session, so it resolves via the shared address (P).
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    pod_a = InvestigationRegistry(sandbox=sandbox, address=addr)
    pod_b = InvestigationRegistry(sandbox=sandbox, address=addr)

    # Nothing published yet ⇒ globally cold (¬P) ⇒ None (durable write is safe).
    assert await pod_b.resolve_io_handle("ws-1") is None

    ha = await pod_a.ensure_handle(await pod_a.session("ws-1"))  # pod A warms + publishes
    # Pod B, with no session of its own, resolves the SAME live handle via the DB.
    assert await pod_b.resolve_io_handle("ws-1") == ha


async def test_rebuild_io_handle_rebuilds_when_the_published_sandbox_was_reaped_492():
    # #492: when a file op hits SandboxNotFound (the published sandbox was reaped),
    # the facade calls rebuild_io_handle INSTEAD of cold-writing durable — it
    # re-acquires a live sandbox (from the archive) and republishes the address.
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, address=addr)

    h1 = await registry.ensure_handle(await registry.session("ws-1"))
    await sandbox.kill(h1)  # host reaps it → a later op would raise SandboxNotFound

    h2 = await registry.rebuild_io_handle("ws-1")
    assert h2 != h1  # a fresh live sandbox
    assert await sandbox.exists(h2, "/") is False  # alive (not SandboxNotFound)
    assert await addr.get("ws-1") == h2  # the shared address now points to the rebuild


async def test_different_investigation_ids_return_distinct_sessions():
    registry = InvestigationRegistry(sandbox=MockSandbox())
    a = await registry.session("ws-1")
    b = await registry.session("ws-2")
    assert a is not b


async def test_ensure_handle_creates_sandbox_on_first_call():
    sandbox = MockSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s = await registry.session("ws-1")
    assert s.handle is None
    handle = await registry.ensure_handle(s)
    assert handle is not None
    assert s.handle is handle


async def test_second_pod_does_not_re_restore_a_live_shared_sandbox_345():
    # #345: two pods share one sandbox backend (the shared vol). Pod A wakes the
    # item cold → restores from the snapshot. Pod B, serving the SAME item later,
    # must NOT re-restore (that would resurrect files the agent deleted) — it
    # reattaches to the already-materialized shared dir. (_RecordingSync is
    # defined below; resolved at call time.)
    sandbox = MockSandbox()  # one backing store shared by both registries
    sync_a, sync_b = _RecordingSync(), _RecordingSync()
    pod_a = InvestigationRegistry(sandbox=sandbox, sync=sync_a)
    pod_b = InvestigationRegistry(sandbox=sandbox, sync=sync_b)

    await pod_a.ensure_handle(await pod_a.session("ws-1"))
    assert sync_a.calls == [("restore", "ws-1")]  # cold → restored once

    await pod_b.ensure_handle(await pod_b.session("ws-1"))
    assert sync_b.calls == []  # already materialized on the shared vol → no re-restore


async def test_ensure_handle_reuses_same_handle_on_second_call():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s = await registry.session("ws-1")
    h1 = await registry.ensure_handle(s)
    h2 = await registry.ensure_handle(s)
    assert h1 is h2
    assert sandbox.create_calls == 1


async def test_concurrent_ensure_handle_calls_create_exactly_once():
    import asyncio

    class _SlowSandbox(_CountingSandbox):
        async def create(self, spec, sandbox_id=None):
            self.create_calls += 1
            await asyncio.sleep(0.01)  # let other coroutines stack up at the lock
            return SandboxHandle(id=f"h-{self.create_calls}")

    sandbox = _SlowSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s = await registry.session("ws-1")

    handles = await asyncio.gather(*[registry.ensure_handle(s) for _ in range(8)])
    assert sandbox.create_calls == 1
    assert all(h is handles[0] for h in handles)


async def test_kill_idle_kills_sessions_past_threshold():
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    # Push the session's last_active 30 minutes into the past.
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    killed = await registry.kill_idle(threshold=timedelta(minutes=15))
    assert killed == ["ws-1"]
    assert sandbox.kill_calls == 1
    # Session is gone — next session() call creates a fresh one.
    new = await registry.session("ws-1")
    assert new is not s


async def test_kill_idle_leaves_recent_sessions_alone():
    from datetime import timedelta

    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)

    killed = await registry.kill_idle(threshold=timedelta(minutes=15))
    assert killed == []
    assert sandbox.kill_calls == 0
    # Session still present.
    assert (await registry.session("ws-1")) is s


async def test_kill_idle_ignores_sessions_with_no_handle():
    """A session that never made a sandbox shouldn't get a kill call,
    but should still be evicted from the registry once idle — otherwise
    the dict grows without bound from every investigation_id ever requested."""
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s = await registry.session("ws-1")
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    killed = await registry.kill_idle(threshold=timedelta(minutes=15))
    assert killed == ["ws-1"]
    assert sandbox.kill_calls == 0  # no handle to kill
    # Eviction still happened.
    new = await registry.session("ws-1")
    assert new is not s


class _ZombieKillSandbox(MockSandbox):
    """MockSandbox whose `kill` raises SandboxNotFound for chosen handle ids —
    mimics the host reaping a sandbox (idle TTL) out from under a live session."""

    def __init__(self) -> None:
        super().__init__()
        self.zombie_ids: set[str] = set()
        self.really_killed: list[str] = []

    async def kill(self, handle: SandboxHandle) -> None:
        if handle.id in self.zombie_ids:
            raise SandboxNotFound(handle.id)  # host already reaped it
        self.really_killed.append(handle.id)
        await super().kill(handle)


class _FlakyMirrorSync:
    """Sync double whose `mirror` raises for chosen workspace ids (an unexpected
    per-item error), succeeds otherwise. `restore` is a no-op."""

    def __init__(self, boom: set[str]) -> None:
        self.boom = boom
        self.mirrored: list[str] = []

    async def restore(self, workspace_id: str, handle: SandboxHandle, *, on_progress=None) -> int:
        return 0

    async def mirror(self, workspace_id: str, handle: SandboxHandle) -> int:
        if workspace_id in self.boom:
            raise RuntimeError("mirror boom")
        self.mirrored.append(workspace_id)
        return 0


async def test_kill_idle_survives_zombie_and_flaky_items_and_reaps_the_rest_366():
    # #366 P7: a per-item failure must not abort the reaper sweep.
    #  - "ws-zombie": host already reaped it → kill raises SandboxNotFound →
    #    treated as done, session cleaned.
    #  - "ws-flaky": an unexpected error (mirror boom) → skipped, session left
    #    for a later retry (NOT lost, NOT crashing the sweep).
    #  - "ws-live": healthy → reaped normally.
    from datetime import UTC, datetime, timedelta

    sandbox = _ZombieKillSandbox()
    sync = _FlakyMirrorSync(boom={"ws-flaky"})
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    sessions = {}
    for wid in ("ws-zombie", "ws-flaky", "ws-live"):
        s = await registry.session(wid)
        await registry.ensure_handle(s)
        s.last_active = datetime.now(UTC) - timedelta(minutes=30)
        sessions[wid] = s
    assert sessions["ws-zombie"].handle is not None
    sandbox.zombie_ids.add(sessions["ws-zombie"].handle.id)

    killed = await registry.kill_idle(threshold=timedelta(minutes=15))

    assert set(killed) == {"ws-zombie", "ws-live"}  # sweep survived the flaky one
    assert sandbox.really_killed == [sessions["ws-live"].handle.id]  # only live truly died
    # zombie cleaned despite its kill raising; live cleaned; flaky left for retry
    assert (await registry.session("ws-zombie")) is not sessions["ws-zombie"]
    assert (await registry.session("ws-live")) is not sessions["ws-live"]
    assert (await registry.session("ws-flaky")) is sessions["ws-flaky"]


async def test_mirror_warm_survives_one_failing_item_366():
    # #366 P7: a mirror that errors on one item must not stop the sweep from
    # mirroring the others (one bad item ≠ a dead sweeper task).
    sync = _FlakyMirrorSync(boom={"ws-bad"})
    registry = InvestigationRegistry(sandbox=MockSandbox(), sync=sync)
    for wid in ("ws-bad", "ws-good"):
        s = await registry.session(wid)
        await registry.ensure_handle(s)

    mirrored = await registry.mirror_warm()

    assert mirrored == ["ws-good"]  # bad item skipped, good one still mirrored
    assert sync.mirrored == ["ws-good"]


class _FakeActivity(IActivityStore):
    """In-memory IActivityStore double.

    It models the WHOLE contract, including the ledger fields the row now
    carries and the `live_for` query built on them — a double that only
    implemented the heartbeat would pass while the real store's per-person tally
    silently regressed, since nothing in the registry reads it back."""

    def __init__(self) -> None:
        self.ms: dict[str, int] = {}
        self.rows: dict[str, LiveSandbox] = {}
        self.owners: dict[str, str] = {}

    async def bump(
        self,
        item_id: str,
        *,
        owner: str = "",
        cpu_milli: int = 0,
        memory_bytes: int = 0,
    ) -> None:
        self.ms[item_id] = 10**13  # far future → counts as "active now"
        self.owners[item_id] = owner
        self.rows[item_id] = LiveSandbox(
            item_id=item_id, cpu_milli=cpu_milli, memory_bytes=memory_bytes
        )

    async def last_active_ms(self, item_id: str) -> int | None:
        return self.ms.get(item_id)

    async def forget(self, item_id: str) -> None:
        self.ms.pop(item_id, None)
        self.rows.pop(item_id, None)
        self.owners.pop(item_id, None)

    async def owner_of(self, item_id: str) -> str | None:
        # The ledger outlives the item record, which is the whole reason this
        # exists — a double that read the item back would model the source it
        # was added to replace.
        return self.owners.get(item_id)

    async def is_live(self, item_id: str, *, since_ms: int) -> bool:
        # Keyed on the ITEM, deliberately — no owner in the question. Asking
        # `live_for(owner_of(item))` made the answer depend on a field anyone
        # with write access can PATCH, so repointing `owner` reported a running
        # sandbox as stopped. A double that answered by owner here would model
        # the contract it replaced rather than the one it stands for.
        return item_id in self.rows and self.ms.get(item_id, 0) >= since_ms

    async def live_for(self, owner: str, *, since_ms: int) -> list[LiveSandbox]:
        return [
            row
            for item, row in self.rows.items()
            if self.owners.get(item) == owner and self.ms.get(item, 0) >= since_ms
        ]


async def test_kill_idle_spares_globally_active_shared_dir_345():
    # #345: this pod is idle on the item, but a GLOBAL heartbeat says another pod
    # touched the shared dir recently → don't rmtree it; just drop our session.
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    activity = _FakeActivity()
    registry = InvestigationRegistry(sandbox=sandbox, activity=activity)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)  # bumps the global heartbeat
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)  # pod-local idle

    killed = await registry.kill_idle(threshold=timedelta(minutes=15))
    assert sandbox.kill_calls == 0  # shared dir NOT torn down
    assert killed == []
    assert (await registry.session("ws-1")) is not s  # local session still dropped


async def test_kill_idle_recycles_globally_idle_shared_dir_345():
    # #345: no pod has touched the dir past the threshold → recycle it
    # (mirror → kill → forget the heartbeat).
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    activity = _FakeActivity()
    registry = InvestigationRegistry(sandbox=sandbox, activity=activity)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    await activity.forget("ws-1")  # heartbeat gone → globally idle
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    killed = await registry.kill_idle(threshold=timedelta(minutes=15))
    assert sandbox.kill_calls == 1
    assert killed == ["ws-1"]
    assert "ws-1" not in activity.ms  # heartbeat forgotten on recycle


async def test_ensure_handle_bumps_global_activity_345():
    sandbox = _CountingSandbox()
    activity = _FakeActivity()
    registry = InvestigationRegistry(sandbox=sandbox, activity=activity)
    await registry.ensure_handle(await registry.session("ws-1"))
    assert "ws-1" in activity.ms  # global heartbeat recorded


async def test_close_all_kills_every_alive_handle():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s1 = await registry.session("ws-1")
    s2 = await registry.session("ws-2")
    await registry.session("ws-3")  # no handle ever created
    await registry.ensure_handle(s1)
    await registry.ensure_handle(s2)

    await registry.close_all()
    assert sandbox.kill_calls == 2  # only the two with handles
    # All sessions cleared.
    new = await registry.session("ws-1")
    assert new is not s1


# ---- sync hooks ----


class _RecordingSync:
    """Stand-in for SandboxSync that records calls so we can assert order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (op, investigation_id)

    async def restore(self, workspace_id, handle, *, on_progress=None):
        self.calls.append(("restore", workspace_id))
        return 0

    async def mirror(self, workspace_id, handle):
        self.calls.append(("mirror", workspace_id))
        return 0


async def test_ensure_handle_calls_sync_restore_after_create():
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    assert sync.calls == [("restore", "ws-1")]
    assert sandbox.create_calls == 1


async def test_ensure_handle_forwards_on_progress_to_sync_restore_492():
    """#492 P11: a turn's restore-progress sink threads ensure_handle → _acquire
    → sync.restore, so a slow cold wake can stream '還原中 N/M' to the turn's
    stream instead of leaving a blank running card."""
    sandbox = _CountingSandbox()
    forwarded: list[object] = []

    class _ProgressSync:
        async def restore(self, workspace_id, handle, *, on_progress=None):
            forwarded.append(on_progress)
            if on_progress is not None:
                on_progress(1, 2)  # simulate one restore tick
            return 0

        async def mirror(self, workspace_id, handle):
            return 0

    registry = InvestigationRegistry(sandbox=sandbox, sync=_ProgressSync())
    s = await registry.session("ws-1")
    ticks: list[tuple[int, int]] = []
    await registry.ensure_handle(s, on_progress=lambda d, t: ticks.append((d, t)))
    # The sink is threaded through to sync.restore …
    assert len(forwarded) == 1 and forwarded[0] is not None
    # … and a tick it emits actually reaches the turn's sink.
    assert ticks == [(1, 2)]


async def test_ensure_handle_skips_restore_when_handle_already_alive():
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    await registry.ensure_handle(s)  # already alive
    # restore only the first time
    assert [c for c in sync.calls if c[0] == "restore"] == [("restore", "ws-1")]


async def test_kill_idle_calls_mirror_before_sandbox_kill():
    from datetime import UTC, datetime, timedelta

    events: list[str] = []

    class _RecordingSandbox(_CountingSandbox):
        async def kill(self, handle):
            events.append("sandbox.kill")
            await super().kill(handle)

    class _RecordingSyncWithLog(_RecordingSync):
        async def mirror(self, workspace_id, handle):
            events.append("sync.mirror")
            return await super().mirror(workspace_id, handle)

    sandbox = _RecordingSandbox()
    sync = _RecordingSyncWithLog()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    await registry.kill_idle(threshold=timedelta(minutes=15))
    assert events == ["sync.mirror", "sandbox.kill"]


async def test_kill_idle_does_not_mirror_for_handleless_sessions():
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    s = await registry.session("ws-1")
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    await registry.kill_idle(threshold=timedelta(minutes=15))
    assert sync.calls == []  # no handle, nothing to mirror


async def test_close_all_mirrors_before_killing_each():
    events: list[str] = []

    class _RecordingSandbox(_CountingSandbox):
        async def kill(self, handle):
            events.append(f"kill:{handle.id}")
            await super().kill(handle)

    class _RecordingSyncWithLog(_RecordingSync):
        async def mirror(self, workspace_id, handle):
            events.append(f"mirror:{workspace_id}")
            return await super().mirror(workspace_id, handle)

    sandbox = _RecordingSandbox()
    sync = _RecordingSyncWithLog()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    s1 = await registry.session("ws-1")
    s2 = await registry.session("ws-2")
    await registry.ensure_handle(s1)
    await registry.ensure_handle(s2)

    await registry.close_all()
    # Each workspace's mirror precedes that workspace's kill.
    mirror_idx_1 = events.index("mirror:ws-1")
    kill_idx_1 = next(
        i for i, e in enumerate(events) if e.startswith("kill:") and s1.handle and s1.handle.id in e
    )
    mirror_idx_2 = events.index("mirror:ws-2")
    kill_idx_2 = next(
        i for i, e in enumerate(events) if e.startswith("kill:") and s2.handle and s2.handle.id in e
    )
    assert mirror_idx_1 < kill_idx_1
    assert mirror_idx_2 < kill_idx_2


# ---- close_session (manual close) ----


async def test_close_session_mirrors_then_kills_then_evicts():
    """Manual close — used by POST /a/{slug}/items/{id}/close — runs
    mirror-sync, kills the sandbox handle, and removes the session
    from the registry."""
    events: list[str] = []

    class _RecordingSandbox(_CountingSandbox):
        async def kill(self, handle):
            events.append("sandbox.kill")
            await super().kill(handle)

    class _RecordingSyncWithLog(_RecordingSync):
        async def mirror(self, workspace_id, handle):
            events.append("sync.mirror")
            return await super().mirror(workspace_id, handle)

    sandbox = _RecordingSandbox()
    sync = _RecordingSyncWithLog()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)

    await registry.close_session("ws-1")
    assert events == ["sync.mirror", "sandbox.kill"]
    new = await registry.session("ws-1")
    assert new is not s


async def test_close_session_is_noop_for_unknown_workspace():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    await registry.close_session("never-touched")
    assert sandbox.kill_calls == 0


async def test_close_session_skips_mirror_when_no_handle():
    """Session was created but ensure_handle never called — no handle
    to kill, no sync.mirror to run, but the session still gets evicted."""
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    s = await registry.session("ws-1")
    await registry.close_session("ws-1")
    assert sandbox.kill_calls == 0
    assert sync.calls == []
    new = await registry.session("ws-1")
    assert new is not s


async def test_close_session_without_sync_just_kills_handle():
    """When the registry was constructed without a sync hook, close_session
    still kills the handle — it just skips the mirror-sync step."""
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    await registry.close_session("ws-1")
    assert sandbox.kill_calls == 1


# ---- throttled mirror (P3) ----


async def test_flush_mirrors_a_warm_session_and_is_noop_when_cold():
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    await registry.flush("ws-1")  # no session → no-op
    s = await registry.session("ws-1")
    await registry.flush("ws-1")  # cold session → no-op
    assert sync.calls == []
    await registry.ensure_handle(s)
    sync.calls.clear()
    await registry.flush("ws-1")  # warm → mirror
    assert sync.calls == [("mirror", "ws-1")]


async def test_mirror_warm_mirrors_only_warm_sessions():
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    warm = await registry.session("ws-warm")
    await registry.ensure_handle(warm)
    await registry.session("ws-cold")  # no handle
    sync.calls.clear()
    mirrored = await registry.mirror_warm()
    assert mirrored == ["ws-warm"]
    assert sync.calls == [("mirror", "ws-warm")]


# ---- scratch-vol du quota sweeper (P5) ----


async def test_ensure_handle_restores_when_backend_not_id_addressable_345():
    # A non-id-addressable backend (handle_for_id None) is always treated as cold
    # by _is_cold (no shared dir to probe), so ensure_handle always restores —
    # the prior per-pod behaviour for that kind.
    class _NoIdSandbox(MockSandbox):
        def handle_for_id(self, sandbox_id):
            return None

    sandbox = _NoIdSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync)
    await registry.ensure_handle(await registry.session("ws-1"))
    assert sync.calls == [("restore", "ws-1")]


async def test_close_session_forgets_global_activity_345():
    sandbox = _CountingSandbox()
    activity = _FakeActivity()
    registry = InvestigationRegistry(sandbox=sandbox, activity=activity)
    await registry.ensure_handle(await registry.session("ws-1"))
    assert "ws-1" in activity.ms
    await registry.close_session("ws-1")
    assert "ws-1" not in activity.ms  # heartbeat forgotten on manual close


async def test_two_http_pods_converge_on_one_address_366():
    # #366: on the http backend two API pods each `create` their own sandbox.
    # With the shared address store, the first pod to claim wins and the second
    # converges on that ONE address instead of keeping a diverging sandbox.
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import (
        SpecstarAddressStore,
        register_sandbox_address,
    )

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)  # one shared address slot for both pods
    sandbox = _HttpStyleSandbox()  # one shared host backend
    pod_a = InvestigationRegistry(sandbox=sandbox, address=addr)
    pod_b = InvestigationRegistry(sandbox=sandbox, address=addr)

    ha = await pod_a.ensure_handle(await pod_a.session("ws-1"))
    hb = await pod_b.ensure_handle(await pod_b.session("ws-1"))

    assert ha == hb  # both pods route to the SAME sandbox address
    assert sandbox.create_calls == 1  # pod B converged — it did NOT mint its own


async def test_ensure_handle_kills_orphan_when_it_loses_the_claim_race_366():
    # #366: two pods both find no address, both create, and race to claim. The
    # loser must kill the sandbox it just created (an orphan) and converge on the
    # winner's address — never leave two diverging sandboxes for one item.
    from workspace_app.api.sandbox_address import IAddressStore

    winner = SandboxHandle(id="winner")

    class _RaceLostAddress(IAddressStore):
        async def get(self, item_id: str) -> SandboxHandle | None:
            return None  # nothing claimed yet → this pod will create

        async def claim(self, item_id: str, handle: SandboxHandle) -> SandboxHandle:
            return winner  # ...but a peer won the race between our get and claim

        async def swap(  # pragma: no cover - unused (no stale address in this path)
            self, item_id: str, expected: SandboxHandle, new: SandboxHandle
        ) -> SandboxHandle:
            return winner

        async def forget(self, item_id: str) -> None:  # pragma: no cover - unused here
            return None

    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, address=_RaceLostAddress())
    h = await registry.ensure_handle(await registry.session("ws-1"))

    assert h == winner  # converged on the race winner
    assert sandbox.create_calls == 1  # we created an orphan...
    assert sandbox.kill_calls == 1  # ...then killed it to converge


async def test_ensure_handle_reacquires_when_host_reaped_the_sandbox_366():
    # #366 face A: the http host reaps the sandbox behind a warm session's handle
    # (30-min idle TTL). The NEXT ensure_handle must detect the dead handle (and
    # the dead shared address) and rebuild — NOT hand back the stale handle that
    # would yield SandboxNotFound on the terminal's exec.
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import (
        SpecstarAddressStore,
        register_sandbox_address,
    )

    spec = SpecStar()
    register_sandbox_address(spec)
    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, address=SpecstarAddressStore(spec))
    s = await registry.session("ws-1")
    h1 = await registry.ensure_handle(s)

    await sandbox.kill(h1)  # host reaps it out from under us

    h2 = await registry.ensure_handle(s)
    assert h2 != h1  # rebuilt a fresh sandbox
    assert await sandbox.exists(h2, "/") is False  # h2 is alive (no SandboxNotFound)
    assert sandbox.create_calls == 2  # created the replacement


# ---- host-managed durable (#492): the host rsyncs its own dir to the NFS
# archive, so the app skips its restore/mirror and writes back via sandbox.persist ----


class _PersistSandbox(_HttpStyleSandbox):
    """#492: an http-style backend that ALSO owns durable — it exposes a
    `persist(handle, *, delete)` the registry calls (the host rsyncs its own
    working dir to/from the NFS archive) in place of the app-side sync.mirror."""

    def __init__(self) -> None:
        super().__init__()
        self.persisted: list[tuple[str, bool]] = []

    async def persist(self, handle: SandboxHandle, *, delete: bool) -> None:
        self.persisted.append((handle.id, delete))


async def test_host_managed_ensure_handle_skips_app_side_restore_492():
    # The host restored the archive into the fresh sandbox during create, so the
    # app must NOT run its own restore (that would fight the host's copy).
    #
    # Both halves are asserted on purpose. "The app ran no restore" alone says
    # only that we correctly did nothing; it stays green however empty the
    # sandbox is, because the component that fills it lives in another process
    # and outside the Sandbox interface. `_HostManagedSandbox` models that
    # component's contract, so the wake can also be held to its OUTCOME.
    tree = Path(tempfile.mkdtemp())
    (tree / "ws-1").mkdir()
    (tree / "ws-1" / "notes.md").write_bytes(b"hi")
    sandbox = _HostManagedSandbox(tree)
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync, host_managed_durable=True)
    handle = await registry.ensure_handle(await registry.session("ws-1"))
    assert sync.calls == []  # no app-side restore in host-managed mode
    assert [e.path for e in (await sandbox.walk(handle, "/")).files] == [
        "/notes.md"
    ]  # host filled it


async def test_host_managed_flush_persists_via_host_with_delete_492():
    # turn-end reconcile ⇒ persist(delete=True) through the host, NOT sync.mirror.
    sandbox = _PersistSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, sync=sync, host_managed_durable=True)
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    await registry.flush("ws-1")
    assert sandbox.persisted == [(h.id, True)]  # host rsync reconcile
    assert [c for c in sync.calls if c[0] == "mirror"] == []  # never the app-side mirror


async def test_host_managed_mirror_warm_is_additive_checkpoint_492():
    # The periodic sweep is an ADDITIVE checkpoint (delete=False) — mid-turn the
    # dir isn't quiesced, so it never reconciles deletions. Also proves the
    # write-back works with NO app-side sync wired (routed via `_has_durable`).
    sandbox = _PersistSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, host_managed_durable=True)
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    mirrored = await registry.mirror_warm()
    assert mirrored == ["ws-1"]
    assert sandbox.persisted == [(h.id, False)]  # additive, never delete mid-turn


async def test_host_managed_kill_idle_persists_before_reap_492():
    from datetime import UTC, datetime, timedelta

    sandbox = _PersistSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, host_managed_durable=True)
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)
    killed = await registry.kill_idle(threshold=timedelta(minutes=15))
    assert killed == ["ws-1"]
    assert sandbox.persisted == [(h.id, True)]  # reconcile (host-side) before rmtree


async def test_host_managed_without_persist_method_is_a_noop_492():
    # Defensive: host-managed is set but the backend exposes no `persist`
    # (misconfig / non-http double) — write-back silently no-ops, never raises.
    sandbox = _CountingSandbox()  # MockSandbox: no persist method
    registry = InvestigationRegistry(sandbox=sandbox, host_managed_durable=True)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    await registry.flush("ws-1")  # must not raise


async def test_http_ensure_handle_reuses_live_handle_without_churn_366():
    # With a shared address wired, a still-live session handle is kept as-is — the
    # liveness probe must NOT rebuild a healthy sandbox on every wake.
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import (
        SpecstarAddressStore,
        register_sandbox_address,
    )

    spec = SpecStar()
    register_sandbox_address(spec)
    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, address=SpecstarAddressStore(spec))
    s = await registry.session("ws-1")
    h1 = await registry.ensure_handle(s)
    h2 = await registry.ensure_handle(s)  # sandbox still alive → keep it
    assert h2 is h1
    assert sandbox.create_calls == 1


class _HostManagedSandbox(MockSandbox):
    """The #492 sandbox-host, modelled by its CONTRACT instead of by its calls.

    The real host restores a fresh sandbox from the durable NFS archive DURING
    `create` (`NfsArchive.restore` = `rsync {nfs_root}/{item_id}/` into the
    working dir) and marks it ready; the app deliberately runs no restore of its
    own. So the only bytes that ever reach a host-managed sandbox are the ones
    PHYSICALLY present in that tree at create time — this double copies exactly
    those, and nothing else.

    A double that merely records the call cannot express that invariant, which
    is how every host-managed test could assert "the app correctly did nothing"
    while the sandbox came up missing the user's files.
    """

    def __init__(self, tree: Path) -> None:
        super().__init__()
        self._tree = tree
        self.persisted: list[tuple[str, bool]] = []

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        handle = await super().create(spec, sandbox_id)
        if sandbox_id is not None:
            src = self._tree / sandbox_id
            if src.is_dir():
                for p in sorted(src.rglob("*")):
                    if p.is_file():
                        rel = "/" + p.relative_to(src).as_posix()
                        self._fs[handle.id][rel] = p.read_bytes()
            await self.mark_ready(handle)
        return handle

    async def persist(self, handle: SandboxHandle, *, delete: bool) -> None:
        self.persisted.append((handle.id, delete))


def _m2_store(tree: Path) -> tuple[MigratingFileStore, MemoryFileStore]:
    """The user's production shape: `sandbox.durable.kind: nfs_tree` with
    `migrate_from: specstar` — writes land in the physical tree, reads fall back
    to the (frozen) legacy store and lazily backfill one file at a time."""
    legacy = MemoryFileStore()
    return MigratingFileStore(NfsTreeFileStore(tree), legacy), legacy


async def test_host_managed_wake_gives_the_sandbox_every_durable_file_492():
    """#492/M2: an item whose files are still only in the LEGACY store must come
    up with its whole workspace.

    The host restores from the physical NFS tree, so a file the app has not yet
    drained into that tree is simply absent from the sandbox — even though the
    dual-read `ls` lists it, so nothing on screen looks wrong. That gap is what
    made a migrated workspace reach the agent with only the handful of files
    somebody happened to open (the lazy read-backfill), and it is invisible to
    any double that only records calls.
    """
    tree = Path(tempfile.mkdtemp())
    files, legacy = _m2_store(tree)
    for path in ("/README.md", "/views/board.ai.yaml", "/.entity/issue/schema.yaml"):
        await legacy.write("ws-1", path, b"x")

    sandbox = _HostManagedSandbox(tree)
    registry = InvestigationRegistry(
        sandbox=sandbox,
        host_managed_durable=True,
        durable_backfill=files.backfill_workspace,
    )
    handle = await registry.ensure_handle(await registry.session("ws-1"))

    assert sorted(e.path for e in (await sandbox.walk(handle, "/")).files) == [
        "/.entity/issue/schema.yaml",
        "/README.md",
        "/views/board.ai.yaml",
    ]


async def test_host_managed_wake_refuses_to_build_a_sandbox_it_cannot_fill_492():
    """#492/M2: when the drain fails, no sandbox is built at all.

    Half a workspace is the one outcome worth failing for: the user would keep
    working on top of the missing files, and the write-back would then stamp
    that partial state over the archive. A wake that raises is recoverable —
    nothing has been created, nothing has been overwritten. Seeding a new item's
    profile stays best-effort by contrast, because template files regenerate and
    the user's do not.
    """

    async def _explode(_item: str) -> int:
        raise OSError("nfs unreachable")

    sandbox = _HostManagedSandbox(Path(tempfile.mkdtemp()))
    registry = InvestigationRegistry(
        sandbox=sandbox,
        host_managed_durable=True,
        durable_backfill=_explode,
    )
    with pytest.raises(OSError, match="nfs unreachable"):
        await registry.ensure_handle(await registry.session("ws-1"))
    assert sandbox._fs == {}  # never created — not even an empty one to work in


async def test_close_session_closes_a_sandbox_this_pod_never_warmed():
    """Closing must not depend on which replica the request landed on.

    `_sessions` is one pod's memory. A close handled by a pod that never woke
    the item found nothing and returned — while the route went on to clear the
    ledger row, so the panel stopped listing an environment that was still
    running. Restarting the backend produces the same state on a single pod:
    the sessions map is empty, the sandbox and its published address are not."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    warm_pod = InvestigationRegistry(sandbox=sandbox, address=addr)
    other_pod = InvestigationRegistry(sandbox=sandbox, address=addr)

    handle = await warm_pod.ensure_handle(await warm_pod.session("ws-1"))
    await sandbox.exists(handle, "/a")  # alive: a live handle answers, dead one raises

    await other_pod.close_session("ws-1")

    with pytest.raises(SandboxNotFound):
        await sandbox.exists(handle, "/a")  # the sandbox outlived its close
    # The address row is deliberately left behind — see
    # `test_close_session_never_erases_the_published_address`. What matters is
    # that it cannot resurrect the dead sandbox: the next acquire probes it,
    # finds it dead, and swaps its own in.
    rebuilt = await other_pod.ensure_handle(await other_pod.session("ws-1"))
    assert rebuilt != handle
    await sandbox.exists(rebuilt, "/a")


async def test_close_session_tolerates_a_sandbox_someone_already_deleted():
    """An operator deleting a sandbox out of band is a supported thing to do.

    `kill` then raises `SandboxNotFound` — which IS the goal, the same reasoning
    `kill_idle` already applies. Letting it propagate meant the session was never
    evicted and the ledger row never cleared, so the panel kept offering a Close
    that could only ever fail: the one entry you could neither use nor remove."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, address=addr)

    session = await registry.session("ws-1")
    handle = await registry.ensure_handle(session)
    await sandbox.kill(handle)  # the operator went in and removed it

    await registry.close_session("ws-1")  # must not raise

    assert await registry.session("ws-1") is not session, "the dead session was kept"
    # The address is deliberately NOT erased. `SandboxNotFound` cannot tell
    # "reaped" from "could not reach it" — the http client maps every transport
    # error onto it — and erasing the address of a sandbox that is actually
    # alive strands it where no pod can find it. Leaving a dead one costs
    # nothing, which the next line demonstrates:
    fresh = await registry.ensure_handle(await registry.session("ws-1"))
    assert fresh != handle, "the next acquire should have rebuilt"
    await sandbox.exists(fresh, "/a")  # …and the rebuilt one is live


async def test_close_session_kills_what_the_address_names_when_the_session_has_no_handle():
    """A session with no handle is the NORMAL state, not an edge case.

    Sandboxes are lazy — only `exec` creates one — so any pod that has served a
    chat turn holds a session whose handle is None. Deciding the teardown on
    "is there a session" rather than "is there a live handle" skipped the kill
    while still erasing the address, which strands the sandbox where no pod can
    address it and lets the next acquire build a SECOND one beside it."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    warm_pod = InvestigationRegistry(sandbox=sandbox, address=addr)
    chat_pod = InvestigationRegistry(sandbox=sandbox, address=addr)

    handle = await warm_pod.ensure_handle(await warm_pod.session("ws-1"))
    await chat_pod.session("ws-1")  # a chat turn: session exists, handle is None

    await chat_pod.close_session("ws-1")

    with pytest.raises(SandboxNotFound):
        await sandbox.exists(handle, "/a")  # it really was killed


async def test_close_session_keeps_every_record_when_it_could_not_finish():
    """A busy host is told to retry — which is only useful if there is still
    something to retry against.

    Anything other than `SandboxNotFound` propagates, and then neither the
    heartbeat nor the address may be touched: clearing them turns "try again in
    a moment" into an environment that is gone from the panel, still running,
    and unreachable."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_activity import SpecstarActivityStore, register_sandbox_activity
    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address
    from workspace_app.sandbox.protocol import SandboxBusy

    spec = SpecStar()
    register_sandbox_address(spec)
    register_sandbox_activity(spec)

    class _BusyOnKill(_HttpStyleSandbox):
        async def kill(self, handle):
            raise SandboxBusy(handle.id)

    sandbox = _BusyOnKill()
    addr = SpecstarAddressStore(spec)
    activity = SpecstarActivityStore(spec)
    registry = InvestigationRegistry(
        sandbox=sandbox, address=addr, activity=activity, owner_of=lambda _i: "alice"
    )
    await registry.ensure_handle(await registry.session("ws-1"))

    with pytest.raises(SandboxBusy):
        await registry.close_session("ws-1")

    assert await addr.get("ws-1") is not None, "the address was erased on a failed close"
    assert await activity.last_active_ms("ws-1") is not None, "the row was cleared anyway"


async def test_close_session_does_not_report_a_kill_it_never_attempted():
    """When the writeback cannot reach the sandbox, the kill has nothing to act
    on — and the address must survive.

    Suppressing `SandboxNotFound` across BOTH steps reported success for a close
    that never tried: `persist` raising on a refused connection skipped the kill
    entirely, cleared both records, and answered 204. The sandbox was alive, the
    panel no longer showed it, and nothing could address it."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    class _UnreachableWriteback:
        async def restore(self, workspace_id, handle, *, on_progress=None):
            return 0

        async def mirror(self, workspace_id, handle):
            raise SandboxNotFound(handle.id)  # what a refused connection becomes

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, address=addr, sync=_UnreachableWriteback())

    handle = await registry.ensure_handle(await registry.session("ws-1"))
    await registry.close_session("ws-1")  # must not raise

    assert await addr.get("ws-1") is not None, "an unconfirmed kill erased the address"
    await sandbox.exists(handle, "/a")  # still alive — nothing claimed otherwise


async def test_close_session_frees_the_slot_when_the_sandbox_is_not_there():
    """`SandboxNotFound` means it is not there, which is the goal.

    That is the reading `kill_idle` and `_alive` already give the same signal,
    and #492 already carved out the case where the sandbox is ALIVE: a
    reachable-but-slow host raises `SandboxBusy`, which clears nothing. Making
    `SandboxNotFound` a third category — "found it, could not confirm" — left an
    operator who had deleted a sandbox out of band with a row that refused to
    clear and a Close button that said so for the whole 8-hour idle window.

    The ADDRESS is a different question and stays where it is; see the next
    test."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_activity import SpecstarActivityStore, register_sandbox_activity
    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    register_sandbox_activity(spec)

    class _AlreadyGone(_HttpStyleSandbox):
        async def kill(self, handle):
            raise SandboxNotFound(handle.id)

    sandbox = _AlreadyGone()
    addr = SpecstarAddressStore(spec)
    activity = SpecstarActivityStore(spec)
    registry = InvestigationRegistry(
        sandbox=sandbox, address=addr, activity=activity, owner_of=lambda _i: "alice"
    )
    await registry.ensure_handle(await registry.session("ws-1"))

    await registry.close_session("ws-1")  # tolerated — it must not raise

    assert await activity.last_active_ms("ws-1") is None, (
        "the owner is still charged for an environment that is not there"
    )


async def test_close_session_never_erases_the_published_address():
    """`kill_idle` does not clear it either, and it does not need to be cleared:
    `_acquire` probes a published address and CAS-swaps a dead one for the
    sandbox it builds, so a stale row costs nothing.

    Deleting it is what carries risk, and the risk cannot be designed away here:
    specstar's `delete` takes no expected-etag, so the delete cannot be made
    conditional on the handle we killed. A peer that rebuilt the item while this
    teardown was in flight would have ITS live address erased, and the next
    acquire would build a second sandbox beside it — the split-brain the address
    store exists to prevent."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, address=addr)
    handle = await registry.ensure_handle(await registry.session("ws-1"))

    await registry.close_session("ws-1")  # a clean, completed kill

    assert await addr.get("ws-1") is not None, "a completed kill still erased the address"
    # …and the stale row costs nothing: the next acquire probes it, finds it
    # dead, and swaps its own in.
    fresh = await registry.ensure_handle(await registry.session("ws-1"))
    assert fresh != handle
    assert await addr.get("ws-1") == fresh


async def test_close_session_frees_the_slot_even_when_it_could_not_find_anything():
    """The case with no way back.

    Nothing named the sandbox: no session on this replica, no published address
    (the pod that created it died before the CAS publish), and the listing
    covers only the host replica that answered. Keeping the row then gives the
    person a 204 that says it worked, a row that stays, and a slot they cannot
    free until the idle window expires — 8 hours by default. Clearing it when we
    are wrong has a way back: the panel asks the backend what is running and
    re-arms what it finds."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_activity import SpecstarActivityStore, register_sandbox_activity

    spec = SpecStar()
    register_sandbox_activity(spec)
    activity = SpecstarActivityStore(spec)
    # A pod that knows nothing about this item: no session, no address store,
    # and a backend whose listing does not name it.
    registry = InvestigationRegistry(
        sandbox=_HttpStyleSandbox(), activity=activity, owner_of=lambda _i: "alice"
    )
    await activity.bump("ws-1", owner="alice", cpu_milli=1000)

    await registry.close_session("ws-1")

    assert await activity.last_active_ms("ws-1") is None, (
        "the owner is charged for something nothing here can reach or close"
    )


async def test_close_session_falls_back_to_the_address_when_its_own_handle_is_stale():
    """A cached handle is this pod's memory, and it can be out of date.

    The host reaps on its own idle timer and pods die, so the handle a session
    holds may name a sandbox that no longer exists while the item has since been
    rebuilt somewhere else — `ensure_handle` already probes for exactly this. A
    close that stopped at the stale handle killed nothing, reported that it had,
    and left the real sandbox running."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    stale_pod = InvestigationRegistry(sandbox=sandbox, address=addr)
    live_pod = InvestigationRegistry(sandbox=sandbox, address=addr)

    old_handle = await stale_pod.ensure_handle(await stale_pod.session("ws-1"))
    await sandbox.kill(old_handle)  # the host reaped it out from under this pod
    await addr.forget("ws-1")
    live = await live_pod.ensure_handle(await live_pod.session("ws-1"))
    assert live != old_handle

    await stale_pod.close_session("ws-1")

    with pytest.raises(SandboxNotFound):
        await sandbox.exists(live, "/a")  # the one that was really running


async def test_close_session_finds_a_sandbox_no_record_names():
    """The last resort, and the only one that can clear an orphan.

    A sandbox whose address was lost — an app pod that died between `create` and
    the CAS publish, a record cleared by an older build of this very method — is
    invisible to every record the app keeps. It keeps running and keeps costing
    its owner, from a row that either is not there or has nothing behind it.
    Asking the backend what it is actually running is the only way to reach it."""
    sandbox = _HttpStyleSandbox()
    orphaning_pod = InvestigationRegistry(sandbox=sandbox)
    handle = await orphaning_pod.ensure_handle(await orphaning_pod.session("ws-1"))
    orphaning_pod._sessions.clear()  # the pod died; nothing records this sandbox

    fresh_pod = InvestigationRegistry(sandbox=sandbox)
    await fresh_pod.close_session("ws-1")

    with pytest.raises(SandboxNotFound):
        await sandbox.exists(handle, "/a")


async def test_close_session_does_not_kill_a_sandbox_belonging_to_another_item():
    """The listing names every sandbox on the answering pod, not just ours.

    Picking the wrong entry closes a stranger's environment mid-turn, which is
    the worst thing this method could do and the reason it matches on item id
    rather than on "the only one running"."""
    sandbox = _HttpStyleSandbox()
    pod = InvestigationRegistry(sandbox=sandbox)
    mine = await pod.ensure_handle(await pod.session("ws-1"))
    theirs = await pod.ensure_handle(await pod.session("ws-2"))
    pod._sessions.clear()

    await InvestigationRegistry(sandbox=sandbox).close_session("ws-1")

    with pytest.raises(SandboxNotFound):
        await sandbox.exists(mine, "/a")
    await sandbox.exists(theirs, "/a")  # untouched


async def test_close_session_writes_back_before_it_gives_up_on_the_kill():
    """The write-back succeeding says nothing about the kill.

    They are separate calls to the same host and only the second frees the
    machine, so the write-back must run and must not be read as a completed
    close: the heartbeat goes (the sandbox is not there), the address stays (we
    did not end it ourselves)."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_activity import SpecstarActivityStore, register_sandbox_activity
    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    register_sandbox_activity(spec)

    class _KillVanishes(_HttpStyleSandbox):
        async def kill(self, handle):
            raise SandboxNotFound(handle.id)

    sandbox = _KillVanishes()
    addr = SpecstarAddressStore(spec)
    activity = SpecstarActivityStore(spec)
    sync = _RecordingSync()
    registry = InvestigationRegistry(
        sandbox=sandbox,
        address=addr,
        activity=activity,
        sync=sync,
        owner_of=lambda _i: "alice",
    )
    await registry.ensure_handle(await registry.session("ws-1"))
    sync.calls.clear()

    await registry.close_session("ws-1")

    assert sync.calls == [("mirror", "ws-1")], "the durable snapshot was skipped"
    assert await activity.last_active_ms("ws-1") is None


async def test_close_session_leaves_a_session_that_woke_up_under_it():
    """The fallbacks run OUTSIDE the session lock, so a turn can re-acquire into
    the same session object while one of them is in flight.

    (The pod's own cached handle is torn down under the lock — that is the #345
    guarantee, and a concurrent file op simply waits and then re-acquires. The
    address and the listing name handles this pod does not own, and holding a
    local lock across a call to another replica would buy nothing.)

    Popping such a session drops a live handle on the floor: the next file op
    builds a fresh sandbox for an item that already has one, and the two working
    dirs diverge. Only a session still holding the `None` this method put there
    may be evicted."""
    sandbox = _HttpStyleSandbox()
    registry = InvestigationRegistry(sandbox=sandbox)
    # A session with no cached handle — the normal state for a pod that served a
    # chat turn — so the teardown comes from the listing, outside the lock.
    session = await registry.session("ws-1")
    orphan = await sandbox.create(SandboxSpec(), sandbox_id="ws-1")

    reacquired = []
    real_kill = sandbox.kill

    async def _wake_mid_teardown(handle):
        if not reacquired:  # stands in for a turn arriving while the kill runs
            reacquired.append(await registry.ensure_handle(session))
        return await real_kill(handle)

    sandbox.kill = _wake_mid_teardown  # ty: ignore[invalid-assignment]
    await registry.close_session("ws-1")

    assert reacquired, "the test never exercised the race it is about"
    assert registry._sessions.get("ws-1") is session, "evicted a session holding a live handle"
    assert session.handle == reacquired[0]
    assert reacquired[0] != orphan


async def test_close_session_kills_what_only_the_address_can_name():
    """The listing cannot stand in for the published address.

    `GET /sandboxes` answers for the ONE host replica that took the request, so
    a sandbox on any other pod is simply absent from it — which is the whole
    reason the address store exists. A close that reached only the listing would
    work or not by which replica the load balancer picked."""
    from specstar import SpecStar

    from workspace_app.api.sandbox_address import SpecstarAddressStore, register_sandbox_address

    spec = SpecStar()
    register_sandbox_address(spec)
    addr = SpecstarAddressStore(spec)
    sandbox = _HttpStyleSandbox()
    warm_pod = InvestigationRegistry(sandbox=sandbox, address=addr)
    other_pod = InvestigationRegistry(sandbox=sandbox, address=addr)

    handle = await warm_pod.ensure_handle(await warm_pod.session("ws-1"))
    sandbox.on_other_replica.add(handle.id)  # the listing will not name it
    assert [e.item_id for e in await sandbox.running_sandboxes() or []] == []

    await other_pod.close_session("ws-1")

    with pytest.raises(SandboxNotFound):
        await sandbox.exists(handle, "/a")


async def test_close_all_keeps_a_sandbox_whose_durable_snapshot_did_not_land():
    """Shutdown must not kill past a failed write-back.

    `kill` can rmtree the item's shared dir (#345), so ending a sandbox whose
    durable snapshot we know is stale trades a slow shutdown for lost work. The
    dir outlives this process either way — the next pod warms it — so stopping
    at that item is free, and the alternative is not."""
    sandbox = _CountingSandbox()

    class _FailingSync(_RecordingSync):
        async def mirror(self, workspace_id, handle):
            raise RuntimeError("the durable store is unreachable")

    registry = InvestigationRegistry(sandbox=sandbox, sync=_FailingSync())
    await registry.ensure_handle(await registry.session("ws-1"))

    await registry.close_all()  # must not raise

    assert sandbox.kill_calls == 0, "killed a sandbox whose snapshot never landed"


async def test_close_all_carries_on_past_one_item_it_could_not_finish():
    """The one path whose whole job is to leave nothing behind used to leak
    every session after the first failure."""
    sandbox = _CountingSandbox()
    real_kill = sandbox.kill

    async def _first_one_fails(handle):
        if sandbox.kill_calls == 0:
            sandbox.kill_calls += 1
            raise RuntimeError("device busy")
        return await real_kill(handle)

    registry = InvestigationRegistry(sandbox=sandbox)
    await registry.ensure_handle(await registry.session("ws-1"))
    await registry.ensure_handle(await registry.session("ws-2"))
    sandbox.kill = _first_one_fails  # ty: ignore[invalid-assignment]

    await registry.close_all()

    assert sandbox.kill_calls == 2, "the second session was stranded by the first"
    assert registry._sessions == {}
