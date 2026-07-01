from workspace_app.api.registry import InvestigationRegistry
from workspace_app.api.sandbox_activity import IActivityStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxNotFound, SandboxSpec


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

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        self.create_calls += 1
        return await super().create(spec, sandbox_id=None)  # ignore id → fresh uuid

    def handle_for_id(self, sandbox_id: str) -> SandboxHandle | None:
        return None

    async def kill(self, handle: SandboxHandle) -> None:
        self.kill_calls += 1
        await super().kill(handle)


async def test_session_for_new_workspace_returns_session_with_no_handle():
    registry = InvestigationRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec())
    session = await registry.session("ws-1")
    assert session.investigation_id == "ws-1"
    assert session.handle is None


async def test_same_investigation_id_returns_same_session_instance():
    registry = InvestigationRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec())
    a = await registry.session("ws-1")
    b = await registry.session("ws-1")
    assert a is b


async def test_peek_handle_routes_to_shared_dir_then_session_handle_345():
    # #345: file ops route through peek_handle. With a shared per-item dir, even
    # a pod with NO local session must route reads to the shared dir (id-derived
    # handle) — the facade falls back to the snapshot if it's cold — instead of
    # reading a stale snapshot. Once this pod warms a session, its handle is used.
    registry = InvestigationRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec())
    derived = registry.peek_handle("ws-1")
    assert derived is not None and derived.id == "ws-1"  # no session, still routable
    session = await registry.session("ws-1")
    handle = await registry.ensure_handle(session)
    assert registry.peek_handle("ws-1") is handle  # this pod's session handle once warm


async def test_peek_handle_is_none_when_sandbox_not_id_addressable_345():
    # An HTTP-style backend mints its own handles (no shared-vol id addressing):
    # peek_handle has nothing to derive before a session, so it stays None and
    # reads fall back to the snapshot — the old per-pod behaviour for that kind.
    class _NoIdSandbox(MockSandbox):
        def handle_for_id(self, sandbox_id):
            return None

    registry = InvestigationRegistry(sandbox=_NoIdSandbox(), default_spec=SandboxSpec())
    assert registry.peek_handle("ws-1") is None


async def test_different_investigation_ids_return_distinct_sessions():
    registry = InvestigationRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec())
    a = await registry.session("ws-1")
    b = await registry.session("ws-2")
    assert a is not b


async def test_ensure_handle_creates_sandbox_on_first_call():
    sandbox = MockSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    pod_a = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync_a)
    pod_b = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync_b)

    await pod_a.ensure_handle(await pod_a.session("ws-1"))
    assert sync_a.calls == [("restore", "ws-1")]  # cold → restored once

    await pod_b.ensure_handle(await pod_b.session("ws-1"))
    assert sync_b.calls == []  # already materialized on the shared vol → no re-restore


async def test_ensure_handle_reuses_same_handle_on_second_call():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")

    handles = await asyncio.gather(*[registry.ensure_handle(s) for _ in range(8)])
    assert sandbox.create_calls == 1
    assert all(h is handles[0] for h in handles)


async def test_kill_idle_kills_sessions_past_threshold():
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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

    async def restore(self, workspace_id: str, handle: SandboxHandle) -> int:
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
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


async def test_enforce_quota_survives_zombie_and_flaky_items_366():
    # #366 P7: same per-item resilience for the over-quota recycler.
    sandbox = _ZombieKillSandbox()
    sync = _FlakyMirrorSync(boom={"ws-flaky"})
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    sessions = {}
    for wid in ("ws-zombie", "ws-flaky", "ws-live"):
        s = await registry.session(wid)
        await registry.ensure_handle(s)
        await sandbox.upload(s.handle, b"x" * 100, "/big.bin")  # push over quota
        sessions[wid] = s
    sandbox.zombie_ids.add(sessions["ws-zombie"].handle.id)

    recycled = await registry.enforce_quota(max_bytes=10)

    assert set(recycled) == {"ws-zombie", "ws-live"}  # flaky skipped, sweep survived
    assert sandbox.really_killed == [sessions["ws-live"].handle.id]
    assert (await registry.session("ws-flaky")) is sessions["ws-flaky"]  # left for retry


async def test_mirror_warm_survives_one_failing_item_366():
    # #366 P7: a mirror that errors on one item must not stop the sweep from
    # mirroring the others (one bad item ≠ a dead sweeper task).
    sync = _FlakyMirrorSync(boom={"ws-bad"})
    registry = InvestigationRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec(), sync=sync)
    for wid in ("ws-bad", "ws-good"):
        s = await registry.session(wid)
        await registry.ensure_handle(s)

    mirrored = await registry.mirror_warm()

    assert mirrored == ["ws-good"]  # bad item skipped, good one still mirrored
    assert sync.mirrored == ["ws-good"]


class _FakeActivity(IActivityStore):
    """In-memory IActivityStore double: item_id → last_active_ms."""

    def __init__(self) -> None:
        self.ms: dict[str, int] = {}

    async def bump(self, item_id: str) -> None:
        self.ms[item_id] = 10**13  # far future → counts as "active now"

    async def last_active_ms(self, item_id: str) -> int | None:
        return self.ms.get(item_id)

    async def forget(self, item_id: str) -> None:
        self.ms.pop(item_id, None)


async def test_kill_idle_spares_globally_active_shared_dir_345():
    # #345: this pod is idle on the item, but a GLOBAL heartbeat says another pod
    # touched the shared dir recently → don't rmtree it; just drop our session.
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    activity = _FakeActivity()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), activity=activity)
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), activity=activity)
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), activity=activity)
    await registry.ensure_handle(await registry.session("ws-1"))
    assert "ws-1" in activity.ms  # global heartbeat recorded


async def test_close_all_kills_every_alive_handle():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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

    async def restore(self, workspace_id, handle):
        self.calls.append(("restore", workspace_id))
        return 0

    async def mirror(self, workspace_id, handle):
        self.calls.append(("mirror", workspace_id))
        return 0


async def test_ensure_handle_calls_sync_restore_after_create():
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    assert sync.calls == [("restore", "ws-1")]
    assert sandbox.create_calls == 1


async def test_ensure_handle_skips_restore_when_handle_already_alive():
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    await registry.kill_idle(threshold=timedelta(minutes=15))
    assert events == ["sync.mirror", "sandbox.kill"]


async def test_kill_idle_does_not_mirror_for_handleless_sessions():
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)

    await registry.close_session("ws-1")
    assert events == ["sync.mirror", "sandbox.kill"]
    new = await registry.session("ws-1")
    assert new is not s


async def test_close_session_is_noop_for_unknown_workspace():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    await registry.close_session("never-touched")
    assert sandbox.kill_calls == 0


async def test_close_session_skips_mirror_when_no_handle():
    """Session was created but ensure_handle never called — no handle
    to kill, no sync.mirror to run, but the session still gets evicted."""
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    await registry.close_session("ws-1")
    assert sandbox.kill_calls == 1


# ---- throttled mirror (P3) ----


async def test_flush_mirrors_a_warm_session_and_is_noop_when_cold():
    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
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
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    warm = await registry.session("ws-warm")
    await registry.ensure_handle(warm)
    await registry.session("ws-cold")  # no handle
    sync.calls.clear()
    mirrored = await registry.mirror_warm()
    assert mirrored == ["ws-warm"]
    assert sync.calls == [("mirror", "ws-warm")]


# ---- scratch-vol du quota sweeper (P5) ----


async def test_enforce_quota_recycles_over_quota_item_345():
    # #345: an item whose shared scratch dir blew past the cap is recycled
    # (mirror → kill → forget heartbeat), so one runaway workspace can't fill
    # the scratch volume the whole fleet shares.
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
    activity = _FakeActivity()
    registry = InvestigationRegistry(
        sandbox=sandbox, default_spec=SandboxSpec(), sync=sync, activity=activity
    )
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    await sandbox.upload(h, b"x" * 100, "/big.bin")  # 100 bytes of scratch

    recycled = await registry.enforce_quota(max_bytes=50)
    assert recycled == ["ws-1"]
    assert events == ["sync.mirror", "sandbox.kill"]  # written back before rmtree
    assert "ws-1" not in activity.ms  # heartbeat forgotten on recycle
    assert (await registry.session("ws-1")) is not s  # session evicted


async def test_enforce_quota_without_sync_or_activity_just_kills_345():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    await sandbox.upload(h, b"x" * 100, "/big.bin")

    recycled = await registry.enforce_quota(max_bytes=50)
    assert recycled == ["ws-1"]
    assert sandbox.kill_calls == 1


async def test_enforce_quota_leaves_under_quota_item_345():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    await sandbox.upload(h, b"x" * 10, "/small.bin")

    recycled = await registry.enforce_quota(max_bytes=1000)
    assert recycled == []
    assert sandbox.kill_calls == 0
    assert (await registry.session("ws-1")) is s


async def test_enforce_quota_disabled_when_max_bytes_zero_345():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    await sandbox.upload(h, b"x" * 10**6, "/huge.bin")

    recycled = await registry.enforce_quota(max_bytes=0)  # 0 ⇒ disabled
    assert recycled == []
    assert sandbox.kill_calls == 0


async def test_enforce_quota_ignores_handleless_sessions_345():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    await registry.session("ws-1")  # no handle ever created
    recycled = await registry.enforce_quota(max_bytes=1)
    assert recycled == []
    assert sandbox.kill_calls == 0


async def test_enforce_quota_treats_a_cold_dir_as_zero_usage_345():
    # The dir went cold (vanished) between sessions — walk raises SandboxNotFound,
    # which counts as 0 bytes (nothing to reap), not a crash.
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")
    h = await registry.ensure_handle(s)
    await sandbox.kill(h)  # dir gone ⇒ subsequent walk raises SandboxNotFound
    sandbox.kill_calls = 0
    recycled = await registry.enforce_quota(max_bytes=1)
    assert recycled == []
    assert sandbox.kill_calls == 0


async def test_ensure_handle_restores_when_backend_not_id_addressable_345():
    # A non-id-addressable backend (handle_for_id None) is always treated as cold
    # by _is_cold (no shared dir to probe), so ensure_handle always restores —
    # the prior per-pod behaviour for that kind.
    class _NoIdSandbox(MockSandbox):
        def handle_for_id(self, sandbox_id):
            return None

    sandbox = _NoIdSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    await registry.ensure_handle(await registry.session("ws-1"))
    assert sync.calls == [("restore", "ws-1")]


async def test_close_session_forgets_global_activity_345():
    sandbox = _CountingSandbox()
    activity = _FakeActivity()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), activity=activity)
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
    pod_a = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), address=addr)
    pod_b = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), address=addr)

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
    registry = InvestigationRegistry(
        sandbox=sandbox, default_spec=SandboxSpec(), address=_RaceLostAddress()
    )
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
    registry = InvestigationRegistry(
        sandbox=sandbox, default_spec=SandboxSpec(), address=SpecstarAddressStore(spec)
    )
    s = await registry.session("ws-1")
    h1 = await registry.ensure_handle(s)

    await sandbox.kill(h1)  # host reaps it out from under us

    h2 = await registry.ensure_handle(s)
    assert h2 != h1  # rebuilt a fresh sandbox
    assert await sandbox.exists(h2, "/") is False  # h2 is alive (no SandboxNotFound)
    assert sandbox.create_calls == 2  # created the replacement


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
    registry = InvestigationRegistry(
        sandbox=sandbox, default_spec=SandboxSpec(), address=SpecstarAddressStore(spec)
    )
    s = await registry.session("ws-1")
    h1 = await registry.ensure_handle(s)
    h2 = await registry.ensure_handle(s)  # sandbox still alive → keep it
    assert h2 is h1
    assert sandbox.create_calls == 1
