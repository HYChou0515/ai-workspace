"""End-to-end persistence test: workspace files survive sandbox restarts.

This is the user-facing promise plan-backend §3.4 exists to deliver.
Glues together InvestigationRegistry (sticky session, restore-after-create,
reverse-before-kill), SandboxSync (the three move ops), MockSandbox
(stand-in for any real adapter), and SpecstarFileStore (durable store).
"""

from datetime import UTC, datetime, timedelta

import pytest

from workspace_app.api.registry import InvestigationRegistry
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxSpec
from workspace_app.sync import SandboxSync


@pytest.fixture
def stack():
    spec = make_spec(default_user="u")
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    registry = InvestigationRegistry(sandbox=sandbox, default_spec=SandboxSpec(), sync=sync)
    return registry, sandbox, filestore


async def test_workspace_files_survive_idle_kill_and_restart(stack):
    registry, sandbox, filestore = stack

    # First "session": agent's shell writes inside the sandbox.
    s = await registry.session("ws")
    h1 = await registry.ensure_handle(s)
    await sandbox.upload(h1, b"shell wrote this", "/output.txt")
    # Sandbox should also see something written via FileStore mid-session.
    await filestore.write("ws", "/notes.md", b"agent jotted")

    # Force idle and let registry tear it down.
    s.last_active = datetime.now(UTC) - timedelta(minutes=30)
    killed = await registry.kill_idle(threshold=timedelta(minutes=15))
    assert killed == ["ws"]

    # Second session: brand-new sandbox handle, restore-after-create
    # should bring both files back.
    s2 = await registry.session("ws")
    h2 = await registry.ensure_handle(s2)
    assert h2.id != h1.id

    cat_output = await sandbox.exec(h2, ["cat", "/output.txt"])
    assert cat_output.stdout == b"shell wrote this"

    cat_notes = await sandbox.exec(h2, ["cat", "/notes.md"])
    assert cat_notes.stdout == b"agent jotted"


async def test_ignored_paths_are_not_persisted(stack):
    registry, sandbox, _ = stack

    s = await registry.session("ws")
    h = await registry.ensure_handle(s)
    await sandbox.upload(h, b"compiled", "/__pycache__/x.cpython-312.pyc")
    await sandbox.upload(h, b"venv lib", "/.venv/lib/python3.12/site.py")
    await sandbox.upload(h, b"real source", "/src/main.py")

    await registry.close_all()

    # Re-create from FileStore-only state.
    s2 = await registry.session("ws")
    h2 = await registry.ensure_handle(s2)
    # Only the non-ignored file came back.
    entries = await sandbox.walk(h2, "/")
    assert sorted(e.path for e in entries) == ["/src/main.py"]
