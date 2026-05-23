from workspace_app.api.registry import InvestigationRegistry
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec


class _CountingSandbox(MockSandbox):
    """MockSandbox that tracks create/kill call counts for the registry tests."""

    def __init__(self) -> None:
        super().__init__()
        self.create_calls = 0
        self.kill_calls = 0

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        self.create_calls += 1
        return await super().create(spec)

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
        async def create(self, spec):
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

    async def flush(self, workspace_id, handle):
        self.calls.append(("flush", workspace_id))
        return 0

    async def reverse(self, workspace_id, handle):
        self.calls.append(("reverse", workspace_id))
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


async def test_kill_idle_calls_reverse_before_sandbox_kill():
    from datetime import UTC, datetime, timedelta

    events: list[str] = []

    class _RecordingSandbox(_CountingSandbox):
        async def kill(self, handle):
            events.append("sandbox.kill")
            await super().kill(handle)

    class _RecordingSyncWithLog(_RecordingSync):
        async def reverse(self, workspace_id, handle):
            events.append("sync.reverse")
            return await super().reverse(workspace_id, handle)

    sandbox = _RecordingSandbox()
    sync = _RecordingSyncWithLog()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    await registry.kill_idle(threshold=timedelta(minutes=15))
    assert events == ["sync.reverse", "sandbox.kill"]


async def test_kill_idle_does_not_reverse_for_handleless_sessions():
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    sync = _RecordingSync()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    s = await registry.session("ws-1")
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)

    await registry.kill_idle(threshold=timedelta(minutes=15))
    assert sync.calls == []  # no handle, nothing to reverse


async def test_close_all_reverses_before_killing_each():
    events: list[str] = []

    class _RecordingSandbox(_CountingSandbox):
        async def kill(self, handle):
            events.append(f"kill:{handle.id}")
            await super().kill(handle)

    class _RecordingSyncWithLog(_RecordingSync):
        async def reverse(self, workspace_id, handle):
            events.append(f"reverse:{workspace_id}")
            return await super().reverse(workspace_id, handle)

    sandbox = _RecordingSandbox()
    sync = _RecordingSyncWithLog()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    s1 = await registry.session("ws-1")
    s2 = await registry.session("ws-2")
    await registry.ensure_handle(s1)
    await registry.ensure_handle(s2)

    await registry.close_all()
    # Each workspace's reverse precedes that workspace's kill.
    reverse_idx_1 = events.index("reverse:ws-1")
    kill_idx_1 = next(
        i for i, e in enumerate(events) if e.startswith("kill:") and s1.handle and s1.handle.id in e
    )
    reverse_idx_2 = events.index("reverse:ws-2")
    kill_idx_2 = next(
        i for i, e in enumerate(events) if e.startswith("kill:") and s2.handle and s2.handle.id in e
    )
    assert reverse_idx_1 < kill_idx_1
    assert reverse_idx_2 < kill_idx_2


# ---- close_session (manual close) ----


async def test_close_session_reverses_then_kills_then_evicts():
    """Manual close — used by POST /investigations/{id}/close — runs
    reverse-sync, kills the sandbox handle, and removes the session
    from the registry."""
    events: list[str] = []

    class _RecordingSandbox(_CountingSandbox):
        async def kill(self, handle):
            events.append("sandbox.kill")
            await super().kill(handle)

    class _RecordingSyncWithLog(_RecordingSync):
        async def reverse(self, workspace_id, handle):
            events.append("sync.reverse")
            return await super().reverse(workspace_id, handle)

    sandbox = _RecordingSandbox()
    sync = _RecordingSyncWithLog()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)

    await registry.close_session("ws-1")
    assert events == ["sync.reverse", "sandbox.kill"]
    new = await registry.session("ws-1")
    assert new is not s


async def test_close_session_is_noop_for_unknown_workspace():
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    await registry.close_session("never-touched")
    assert sandbox.kill_calls == 0


async def test_close_session_skips_reverse_when_no_handle():
    """Session was created but ensure_handle never called — no handle
    to kill, no sync.reverse to run, but the session still gets evicted."""
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
    still kills the handle — it just skips the reverse-sync step."""
    sandbox = _CountingSandbox()
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")
    await registry.ensure_handle(s)
    await registry.close_session("ws-1")
    assert sandbox.kill_calls == 1
