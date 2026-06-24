from dataclasses import dataclass

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
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient as ApiTestClient


def register_rca_item(spec: SpecStar, **fields: object) -> str:
    """Create a real rca App item directly via the resource manager (no file
    seeding) and return its id. The workspace routes validate slug→item (#95),
    so own-client tests use this instead of an arbitrary synthetic id."""
    data = {"title": "t", "owner": "u", **fields}
    return (
        spec.get_resource_manager(RcaInvestigation)
        .create(
            RcaInvestigation(**data)  # ty: ignore[invalid-argument-type]
        )
        .resource_id
    )


@dataclass
class Harness:
    # `client` auto-prefixes /api (#177) so existing call sites use bare paths;
    # `spa_client` is the raw client for SPA-fallback / openapi-shape assertions.
    client: ApiTestClient
    spa_client: TestClient
    spec: SpecStar
    filestore: SpecstarFileStore
    iid: str  # a real rca App item; workspace routes validate slug→item (#95)

    def wpath(self, suffix: str = "") -> str:
        """Build a workspace route path for this harness's item — the routes
        nest under /a/{slug}/items/{item_id} (#95). `suffix` starts with '/'."""
        return f"/a/rca/items/{self.iid}{suffix}"


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
    spec = make_spec()
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    runner = ScriptedAgentRunner(scripted_events)
    app = create_app(spec=spec, sandbox=sandbox, filestore=filestore, runner=runner)
    # A real rca App item so the workspace routes' slug→item validation (#95)
    # passes. Created via the resource manager (not the seeding endpoint) so the
    # workspace starts empty — file-listing tests see only what they write.
    iid = (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="t", owner="u"))
        .resource_id
    )
    return Harness(
        client=ApiTestClient(app),
        spa_client=TestClient(app),
        spec=spec,
        filestore=filestore,
        iid=iid,
    )
