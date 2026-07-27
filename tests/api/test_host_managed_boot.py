"""#492: host-managed-durable boot guard.

When the app is told the host owns durable (host_managed_durable=True) but the
sandbox backend can't `persist`, the registry's write-back would silently no-op
and NOTHING would ever reach durable — a silent data-loss footgun. create_app
must refuse to start instead, so a misconfig fails loud at boot.
"""

from __future__ import annotations

import pytest

from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle


def _runner() -> ScriptedAgentRunner:
    return ScriptedAgentRunner([RunDone()])


def test_host_managed_without_persist_op_fails_loud_at_boot():
    with pytest.raises(RuntimeError, match="persist"):
        create_app(
            spec=make_spec(default_user="u"),
            sandbox=MockSandbox(),  # no `persist` op
            filestore=MemoryFileStore(),
            runner=_runner(),
            host_managed_durable=True,
        )


def test_host_managed_with_a_persist_op_boots():
    class _PersistMock(MockSandbox):
        async def persist(self, handle: SandboxHandle, *, delete: bool) -> None:  # pragma: no cover
            return None

    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=_PersistMock(),
        filestore=MemoryFileStore(),
        runner=_runner(),
        host_managed_durable=True,
    )
    assert app is not None


def test_default_off_boots_with_a_plain_sandbox():
    # The guard only fires when host_managed_durable is set — the default path is
    # unchanged (a plain MockSandbox without persist boots fine).
    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_runner(),
    )
    assert app is not None


def test_drain_hook_is_wired_only_while_durable_still_spans_two_stores():
    """#492/M2: the host restores a sandbox from the physical durable tree, so an
    item still half-migrated has to be drained into that tree before create.

    That only applies while `migrate_from` has durable split across two backends.
    A store that IS the tree (migration retired) has nothing to drain, and a
    non-host-managed deployment restores through the FileStore itself — which
    already unions both — so neither gets the hook.
    """
    from workspace_app.api.app import resolve_durable_backfill
    from workspace_app.filestore.migrating import MigratingFileStore
    from workspace_app.filestore.nfs_tree import NfsTreeFileStore

    migrating = MigratingFileStore(NfsTreeFileStore("/tmp/tree"), MemoryFileStore())
    plain = NfsTreeFileStore("/tmp/tree")

    assert resolve_durable_backfill(migrating, host_managed_durable=True) is not None
    assert resolve_durable_backfill(plain, host_managed_durable=True) is None
    assert resolve_durable_backfill(migrating, host_managed_durable=False) is None
