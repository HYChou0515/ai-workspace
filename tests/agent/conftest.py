import pytest
from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec
from workspace_app.sync import SandboxSync


@pytest.fixture
def ctx() -> RunContextWrapper[AgentToolContext]:
    """An RCA tool context wired like the real app: file ops go through a
    liveness-routing WorkspaceFiles facade, and the first exec wakes the
    sandbox (create + restore the snapshot into it)."""
    spec = make_spec(default_user="test-user")
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    holder: dict[str, SandboxHandle] = {}

    async def _resolve(ws: str) -> SandboxHandle | None:
        return holder.get(ws)

    files = WorkspaceFiles(filestore, sandbox, _resolve)

    async def wake(on_progress=None) -> SandboxHandle:
        h = await sandbox.create(SandboxSpec())
        await sync.restore("ws-test", h, on_progress=on_progress)
        holder["ws-test"] = h
        return h

    return RunContextWrapper(
        AgentToolContext(
            investigation_id="ws-test",
            sandbox=sandbox,
            filestore=filestore,
            files=files,
            sync=sync,
            ensure_sandbox_via=wake,
        )
    )
