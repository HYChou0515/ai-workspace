from workspace_app.api.registry import WorkspaceRegistry
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
    registry = WorkspaceRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec())
    session = await registry.session("ws-1")
    assert session.workspace_id == "ws-1"
    assert session.handle is None


async def test_same_workspace_id_returns_same_session_instance():
    registry = WorkspaceRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec())
    a = await registry.session("ws-1")
    b = await registry.session("ws-1")
    assert a is b


async def test_different_workspace_ids_return_distinct_sessions():
    registry = WorkspaceRegistry(sandbox=MockSandbox(), default_spec=SandboxSpec())
    a = await registry.session("ws-1")
    b = await registry.session("ws-2")
    assert a is not b


async def test_ensure_handle_creates_sandbox_on_first_call():
    sandbox = MockSandbox()
    registry = WorkspaceRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")
    assert s.handle is None
    handle = await registry.ensure_handle(s)
    assert handle is not None
    assert s.handle is handle


async def test_ensure_handle_reuses_same_handle_on_second_call():
    sandbox = _CountingSandbox()
    registry = WorkspaceRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    registry = WorkspaceRegistry(sandbox=sandbox, default_spec=SandboxSpec())
    s = await registry.session("ws-1")

    handles = await asyncio.gather(*[registry.ensure_handle(s) for _ in range(8)])
    assert sandbox.create_calls == 1
    assert all(h is handles[0] for h in handles)


async def test_kill_idle_kills_sessions_past_threshold():
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    registry = WorkspaceRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    registry = WorkspaceRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    the dict grows without bound from every workspace_id ever requested."""
    from datetime import UTC, datetime, timedelta

    sandbox = _CountingSandbox()
    registry = WorkspaceRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
    registry = WorkspaceRegistry(sandbox=sandbox, default_spec=SandboxSpec())
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
