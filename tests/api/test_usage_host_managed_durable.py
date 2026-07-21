"""The usage number on a HOST-MANAGED-DURABLE deployment (#538 follow-up).

This is the branch production takes (`kind: http` + host-managed durable), and
it behaves differently from every other test's in exactly the way that matters:

    # registry._writeback
    if self.host_managed_durable:
        persist = getattr(self.sandbox, "persist", None)
        if persist is not None:
            await persist(handle, delete=delete)
        return                               # <-- SandboxSync.mirror never runs
    await self.sync.mirror(inv_id, handle)

`on_measured` — and therefore `record_measurement` — lives inside
`SandboxSync.mirror`, so on this branch the facade's measurement cache is
*never* refreshed from outside. It is only ever filled by a read that measures
inline, and then answers from that value for a whole `usage_window`.

An agent's `exec` writes straight into the sandbox, so nothing invalidates that
cache. Turn end — the one moment the FE refetches the usage bar — lands inside
the window, so the bar shows a pre-turn number and then nothing asks again.

Every other harness in this suite runs the `sync.mirror` branch, where
`on_measured` refreshes the cache and hides all of this. Hence a dedicated
fixture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workspace_app.api import (
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    create_app,
)
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle

from ._client import TestClient as ApiTestClient
from .conftest import register_rca_item


class _PersistingSandbox(MockSandbox):
    """A sandbox that owns its durable copy, like the HTTP host does.

    `create_app` refuses to start host-managed-durable without a `persist` op
    (a backend that owns durable but cannot be asked to write it back would
    lose every workspace), so the flag and this method go together.
    """

    def __init__(self) -> None:
        super().__init__()
        self.persist_calls = 0
        self.handle: SandboxHandle | None = None

    async def create(self, *args, **kwargs) -> SandboxHandle:
        self.handle = await super().create(*args, **kwargs)
        return self.handle

    async def persist(self, handle: SandboxHandle, *, delete: bool) -> None:
        self.persist_calls += 1


@pytest.fixture
def host_managed():
    spec = make_spec()
    sandbox = _PersistingSandbox()
    filestore = SpecstarFileStore(spec)
    runner = ScriptedAgentRunner([MessageDelta(text="done"), RunDone()])
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=filestore,
        runner=runner,
        host_managed_durable=True,
    )
    iid = register_rca_item(spec)
    return app, ApiTestClient(app), TestClient(app), sandbox, iid


def _usage(client, iid: str) -> int:
    return client.get(f"/a/rca/items/{iid}/files/usage").json()["used"]


def _warm(client, iid: str) -> None:
    """Bring the sandbox up — a measurement is only served for a warm workspace."""
    client.post(f"/a/rca/items/{iid}/exec", json={"cmd": ["echo", "warm"]})


async def _write_behind_the_facade(sandbox, iid: str, data: bytes) -> None:
    """What `exec` does: put bytes in the sandbox without telling the facade."""
    assert sandbox.handle is not None, "warm the workspace first"
    await sandbox.upload(sandbox.handle, data, "/exec-output.bin")


def test_the_mirror_never_runs_on_this_branch(host_managed):
    """Guards the premise. If this ever fails, `on_measured` is refreshing the
    cache again and the two tests below stop testing what they claim to."""
    _app, client, _spa, sandbox, iid = host_managed
    _warm(client, iid)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    assert sandbox.persist_calls > 0, "host-managed durable should persist via the host"


@pytest.mark.anyio
async def test_a_turn_leaves_the_usage_number_current(host_managed):
    _app, client, spa, sandbox, iid = host_managed
    _warm(client, iid)
    before = _usage(client, iid)  # measures inline and caches the result

    await _write_behind_the_facade(sandbox, iid, b"x" * 4096)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})

    assert _usage(client, iid) > before, (
        "the bytes the turn wrote are missing — the usage bar shows a pre-turn number"
    )


@pytest.mark.anyio
async def test_a_terminal_command_leaves_the_usage_number_current(host_managed):
    _app, client, spa, sandbox, iid = host_managed
    _warm(client, iid)
    before = _usage(client, iid)

    await _write_behind_the_facade(sandbox, iid, b"y" * 4096)
    client.post(f"/a/rca/items/{iid}/exec", json={"cmd": ["echo", "did-something"]})

    assert _usage(client, iid) > before, (
        "the bytes the command wrote are missing from the usage number"
    )
