import json

from specstar import QB

from workspace_app.resources import Conversation, Workspace

from .conftest import Harness


def _parse_sse(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))
    return events


def test_post_message_returns_sse_stream(harness: Harness):
    response = harness.client.post("/workspaces/ws-1/messages", json={"content": "hello"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_post_message_streams_all_scripted_events(harness: Harness):
    response = harness.client.post("/workspaces/ws-1/messages", json={"content": "hello"})
    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types == ["tool_start", "tool_end", "message_delta", "message_delta", "done"]


def test_post_message_appends_to_conversation(harness: Harness):
    # Create another workspace first so the conversation-lookup loop has to
    # skip a non-matching entry — exercises the false branch of the inner if.
    harness.client.post("/workspaces/ws-other/messages", json={"content": "ignored"})
    harness.client.post("/workspaces/ws-1/messages", json={"content": "first"})
    harness.client.post("/workspaces/ws-1/messages", json={"content": "second"})
    rm = harness.spec.get_resource_manager(Conversation)
    convs: list[Conversation] = []
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        data = r.data
        assert isinstance(data, Conversation)
        if data.workspace_id == "ws-1":
            convs.append(data)
    assert len(convs) == 1
    assert [m.content for m in convs[0].messages] == ["first", "second"]


def test_workspace_crud_via_specstar_routes(harness: Harness):
    resp = harness.client.post("/workspace", json={"name": "demo"})
    assert resp.status_code == 200
    rm = harness.spec.get_resource_manager(Workspace)
    assert rm.count_resources(QB.all()) == 1  # ty: ignore[invalid-argument-type]


def test_create_app_works_without_explicit_spec():
    from fastapi.testclient import TestClient

    from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
    from workspace_app.sandbox.mock import MockSandbox

    # Need a separate spec for the filestore since create_app makes its own
    fs_spec_holder: list = []

    class _FS:
        async def write(self, *a, **k): ...
        async def read(self, *a, **k):
            return b""

        async def ls(self, *a, **k):
            return []

        async def exists(self, *a, **k):
            return False

        async def delete(self, *a, **k): ...

    app = create_app(
        sandbox=MockSandbox(),
        filestore=_FS(),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    assert TestClient(app).post("/workspaces/x/messages", json={"content": "y"}).status_code == 200


def test_runner_exception_is_emitted_as_error_event():
    from fastapi.testclient import TestClient

    from workspace_app.api import create_app
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox

    class _Boom:
        async def run(self, prompt, ctx):
            raise RuntimeError("boom")
            yield  # pragma: no cover — makes this an async generator

    from datetime import UTC, datetime

    from specstar import SpecStar

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_Boom(),
    )
    resp = TestClient(app).post("/workspaces/x/messages", json={"content": "y"})
    body = resp.text
    assert "error" in body
    assert "boom" in body
