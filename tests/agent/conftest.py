from datetime import UTC, datetime

import pytest
from agents import RunContextWrapper
from specstar import SpecStar

from workspace_app.agent import AgentToolContext
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sync import SandboxSync


@pytest.fixture
def ctx() -> RunContextWrapper[AgentToolContext]:
    spec = SpecStar()
    spec.configure(default_user="test-user", default_now=lambda: datetime.now(UTC))
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="ws-test",
            sandbox=sandbox,
            filestore=filestore,
            sync=SandboxSync(filestore=filestore, sandbox=sandbox),
        )
    )
