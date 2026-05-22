from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.api import (
    AgentEvent,
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    ToolEnd,
    ToolStart,
    create_app,
)
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.sandbox.mock import MockSandbox


@dataclass
class Harness:
    client: TestClient
    spec: SpecStar


@pytest.fixture
def scripted_events() -> list[AgentEvent]:
    return [
        ToolStart(call_id="c1", name="exec", args={"cmd": ["echo", "hi"]}),
        ToolEnd(call_id="c1", output="exit_code=0\n--- stdout ---\nhi"),
        MessageDelta(text="Done. "),
        MessageDelta(text="The file printed 'hi'."),
        RunDone(),
    ]


@pytest.fixture
def harness(scripted_events: list[AgentEvent]) -> Harness:
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    runner = ScriptedAgentRunner(scripted_events)
    app = create_app(spec=spec, sandbox=sandbox, filestore=filestore, runner=runner)
    return Harness(client=TestClient(app), spec=spec)
