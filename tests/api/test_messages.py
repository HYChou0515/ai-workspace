import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import QB, SpecStar

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, Investigation
from workspace_app.sandbox.mock import MockSandbox

from .conftest import Harness


def test_reasoning_delta_persists_to_reasoning_channel():
    """A <think> reasoning delta is stored on the assistant message's
    reasoning field, separate from the visible answer."""
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    runner = ScriptedAgentRunner(
        [
            MessageDelta(text="weighing the options", reasoning=True),
            MessageDelta(text="The answer is 42."),
            RunDone(),
        ]
    )
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    client = TestClient(app)
    client.post("/investigations/ws-z/messages", json={"content": "q"})
    rm = spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.investigation_id == "ws-z"
    )
    assistant = next(m for m in conv.messages if m.role == "assistant")
    assert assistant.content == "The answer is 42."
    assert assistant.reasoning == "weighing the options"


def _parse_sse(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))
    return events


def test_post_message_returns_sse_stream(harness: Harness):
    response = harness.client.post("/investigations/ws-1/messages", json={"content": "hello"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_post_message_streams_all_scripted_events(harness: Harness):
    response = harness.client.post("/investigations/ws-1/messages", json={"content": "hello"})
    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types == ["tool_start", "tool_end", "message_delta", "message_delta", "done"]


def test_post_message_appends_to_conversation(harness: Harness):
    # Create another workspace first so the conversation-lookup loop has to
    # skip a non-matching entry — exercises the false branch of the inner if.
    harness.client.post("/investigations/ws-other/messages", json={"content": "ignored"})
    harness.client.post("/investigations/ws-1/messages", json={"content": "first"})
    rm = harness.spec.get_resource_manager(Conversation)
    convs: list[Conversation] = []
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        data = r.data
        assert isinstance(data, Conversation)
        if data.investigation_id == "ws-1":
            convs.append(data)
    assert len(convs) == 1
    roles = [(m.role, m.content) for m in convs[0].messages]
    assert roles[0] == ("user", "first")
    # the scripted reply + tool output persist too, so re-entering the
    # workspace restores the full turn, not just the user's message.
    assert ("tool", "exit_code=0\n--- stdout ---\nhi") in roles
    assert ("assistant", "Done. The file printed 'hi'.") in roles


def test_assistant_reply_persists_for_reload(harness: Harness):
    """The streamed assistant text is concatenated into one persisted
    assistant message (so a re-entry shows the agent's reply)."""
    harness.client.post("/investigations/ws-r/messages", json={"content": "hi"})
    rm = harness.spec.get_resource_manager(Conversation)
    conv = next(
        r.data
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Conversation) and r.data.investigation_id == "ws-r"
    )
    assistant = [m for m in conv.messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == "Done. The file printed 'hi'."


def test_investigation_crud_via_specstar_routes(harness: Harness):
    resp = harness.client.post("/investigation", json={"title": "demo", "owner": "default-user"})
    assert resp.status_code == 200
    rm = harness.spec.get_resource_manager(Investigation)
    assert rm.count_resources(QB.all()) == 1  # ty: ignore[invalid-argument-type]


def test_get_templates_lists_profiles(harness: Harness):
    resp = harness.client.get("/templates")
    assert resp.status_code == 200
    profiles = resp.json()
    assert "default" in profiles
    assert "smt-reflow-example" in profiles


def test_post_investigation_seeds_default_files(harness: Harness):
    """Creating with the default profile seeds at least one starter file
    (its content is user-owned, so we don't pin the exact filenames)."""
    resp = harness.client.post(
        "/investigation",
        json={"title": "Solder voids spike", "owner": "alice"},
    )
    assert resp.status_code == 200
    inv_id = resp.json()["resource_id"]

    files_resp = harness.client.get(f"/investigations/{inv_id}/files")
    assert files_resp.status_code == 200
    paths = {item["path"] for item in files_resp.json()}
    assert len(paths) > 0


def test_post_investigation_methodology_profile_seeds_skeleton(harness: Harness):
    resp = harness.client.post(
        "/investigation",
        json={"title": "x", "owner": "alice", "template_profile": "methodology"},
    )
    inv_id = resp.json()["resource_id"]
    paths = {it["path"] for it in harness.client.get(f"/investigations/{inv_id}/files").json()}
    assert paths == {"/brief.md", "/5-why.md", "/fishbone.canvas", "/report.v1.md"}


def test_post_investigation_with_example_profile_seeds_rich_kit(harness: Harness):
    resp = harness.client.post(
        "/investigation",
        json={
            "title": "Solder voids spike",
            "owner": "alice",
            "template_profile": "smt-reflow-example",
        },
    )
    assert resp.status_code == 200
    inv_id = resp.json()["resource_id"]
    paths = {it["path"] for it in harness.client.get(f"/investigations/{inv_id}/files").json()}
    assert "/drift.ipynb" in paths
    assert "/data/reflow.zone3.sample.csv" in paths


def test_post_investigation_unknown_profile_returns_422(harness: Harness):
    resp = harness.client.post(
        "/investigation",
        json={"title": "x", "owner": "alice", "template_profile": "nope"},
    )
    assert resp.status_code == 422


def test_post_investigation_substitutes_brief_md(harness: Harness):
    resp = harness.client.post(
        "/investigation",
        json={
            "title": "Crack at flange",
            "owner": "carol",
            "description": "5 of 240 cracked at injection-point.",
            "product": "Housing G2",
            "severity": "P1",
            "template_profile": "methodology",
        },
    )
    assert resp.status_code == 200
    inv_id = resp.json()["resource_id"]
    brief = harness.client.get(f"/investigations/{inv_id}/files/brief.md").text
    assert "Crack at flange" in brief
    assert "carol" in brief
    assert "Housing G2" in brief
    assert "P1" in brief
    assert "5 of 240 cracked" in brief


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
    resp = TestClient(app).post("/investigations/x/messages", json={"content": "y"})
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
        async def mkdir(self, *a, **k): ...
        async def rmdir(self, *a, **k): ...

        async def is_dir(self, *a, **k):
            return False

        async def listdir(self, *a, **k):
            return []

        def dirty_paths(self, *a, **k):
            return set()

        def clear_dirty(self, *a, **k): ...

    app = create_app(
        sandbox=MockSandbox(),
        filestore=_FS(),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    assert (
        TestClient(app).post("/investigations/x/messages", json={"content": "y"}).status_code == 200
    )


async def test_list_files_returns_path_size_pairs(harness: Harness):
    await harness.filestore.write("ws-files", "/a.txt", b"hello")
    await harness.filestore.write("ws-files", "/sub/b.txt", b"world!")

    resp = harness.client.get("/investigations/ws-files/files")
    assert resp.status_code == 200
    by_path = {it["path"]: it["size"] for it in resp.json()}
    assert by_path == {"/a.txt": 5, "/sub/b.txt": 6}


async def test_list_files_prefix_filter(harness: Harness):
    await harness.filestore.write("ws-files", "/src/a.py", b"a")
    await harness.filestore.write("ws-files", "/src/b.py", b"b")
    await harness.filestore.write("ws-files", "/README", b"r")
    resp = harness.client.get("/investigations/ws-files/files?prefix=/src/")
    paths = [it["path"] for it in resp.json()]
    assert sorted(paths) == ["/src/a.py", "/src/b.py"]


async def test_read_file_returns_text_for_utf8(harness: Harness):
    await harness.filestore.write("ws-files", "/a.txt", b"hello")
    resp = harness.client.get("/investigations/ws-files/files/a.txt")
    assert resp.status_code == 200
    assert resp.content == b"hello"
    assert resp.headers["content-type"].startswith("text/plain")


async def test_read_file_returns_octet_stream_for_binary(harness: Harness):
    await harness.filestore.write("ws-files", "/bin", b"\xff\xfe\x00\x01")
    resp = harness.client.get("/investigations/ws-files/files/bin")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")


def test_read_file_missing_returns_404(harness: Harness):
    resp = harness.client.get("/investigations/ws-files/files/nope")
    assert resp.status_code == 404


async def test_put_file_writes_raw_bytes(harness: Harness):
    """PUT /investigations/{id}/files/{path} stores raw bytes; the FE
    auto-saves notebooks via this endpoint."""
    resp = harness.client.put("/investigations/ws-put/files/notes.txt", content=b"hello world")
    assert resp.status_code == 204
    # Round-trip through the public read endpoint.
    got = harness.client.get("/investigations/ws-put/files/notes.txt")
    assert got.status_code == 200
    assert got.content == b"hello world"


async def test_put_file_overwrites(harness: Harness):
    harness.client.put("/investigations/ws-put/files/x", content=b"first")
    harness.client.put("/investigations/ws-put/files/x", content=b"second")
    got = harness.client.get("/investigations/ws-put/files/x")
    assert got.content == b"second"


async def test_put_file_into_nested_path(harness: Harness):
    """Path segments are preserved verbatim — FE uses this to save
    notebook files at /report.v3.md, /data/foo.csv, etc."""
    harness.client.put("/investigations/ws-put/files/report.v3.md", content=b"# v3")
    got = harness.client.get("/investigations/ws-put/files/report.v3.md")
    assert got.content == b"# v3"


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
    resp = TestClient(app).post("/investigations/x/messages", json={"content": "y"})
    body = resp.text
    assert "error" in body
    assert "boom" in body
