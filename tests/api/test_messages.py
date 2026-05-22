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


def test_spa_index_served_at_root_when_dist_exists():
    """If web/dist has been built, GET / returns the React app's index.html."""
    from pathlib import Path

    spa_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if not (spa_dist / "index.html").is_file():
        import pytest

        pytest.skip("web/dist not built")

    from datetime import UTC, datetime

    from fastapi.testclient import TestClient
    from specstar import SpecStar

    from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert b'<div id="root">' in resp.content


def test_spa_mount_skipped_when_dist_missing(tmp_path):
    """create_app must not crash when the SPA build directory is absent."""
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient
    from specstar import SpecStar

    from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
        spa_dist=tmp_path / "does-not-exist",
    )
    # POST messages still works.
    resp = TestClient(app).post("/workspaces/x/messages", json={"content": "y"})
    assert resp.status_code == 200


def test_create_app_works_without_explicit_spec():
    from fastapi.testclient import TestClient

    from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
    from workspace_app.sandbox.mock import MockSandbox

    class _FS:
        async def write(self, *a, **k): ...
        async def read(self, *a, **k):
            return b""

        async def ls(self, *a, **k):
            return []

        async def exists(self, *a, **k):
            return False

        async def delete(self, *a, **k): ...

        def dirty_paths(self, *a, **k):
            return set()

        def clear_dirty(self, *a, **k): ...

    app = create_app(
        sandbox=MockSandbox(),
        filestore=_FS(),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    assert TestClient(app).post("/workspaces/x/messages", json={"content": "y"}).status_code == 200


async def test_list_files_returns_path_size_pairs(harness: Harness):
    await harness.filestore.write("ws-files", "/a.txt", b"hello")
    await harness.filestore.write("ws-files", "/sub/b.txt", b"world!")

    resp = harness.client.get("/workspaces/ws-files/files")
    assert resp.status_code == 200
    by_path = {it["path"]: it["size"] for it in resp.json()}
    assert by_path == {"/a.txt": 5, "/sub/b.txt": 6}


async def test_list_files_prefix_filter(harness: Harness):
    await harness.filestore.write("ws-files", "/src/a.py", b"a")
    await harness.filestore.write("ws-files", "/src/b.py", b"b")
    await harness.filestore.write("ws-files", "/README", b"r")
    resp = harness.client.get("/workspaces/ws-files/files?prefix=/src/")
    paths = [it["path"] for it in resp.json()]
    assert sorted(paths) == ["/src/a.py", "/src/b.py"]


async def test_read_file_returns_text_for_utf8(harness: Harness):
    await harness.filestore.write("ws-files", "/a.txt", b"hello")
    resp = harness.client.get("/workspaces/ws-files/files/a.txt")
    assert resp.status_code == 200
    assert resp.content == b"hello"
    assert resp.headers["content-type"].startswith("text/plain")


async def test_read_file_returns_octet_stream_for_binary(harness: Harness):
    await harness.filestore.write("ws-files", "/bin", b"\xff\xfe\x00\x01")
    resp = harness.client.get("/workspaces/ws-files/files/bin")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")


def test_read_file_missing_returns_404(harness: Harness):
    resp = harness.client.get("/workspaces/ws-files/files/nope")
    assert resp.status_code == 404


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
