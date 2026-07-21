"""DELETE /files/{path} + POST /files/move — backs the file-tree
right-click menu (Delete / Rename / Move).
"""

from __future__ import annotations

import asyncio

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.protocol import FileNotFound
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxBusy

from ._client import TestClient as ApiTestClient
from .conftest import Harness, register_rca_item


def test_upload_over_single_file_cap_returns_413():
    # #219: the upload streams to a staging file and rejects mid-stream once the
    # cap is exceeded — a 10-byte body against an 8-byte cap is 413, while an
    # under-cap upload still succeeds.
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        max_file_size=8,
    )
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    over = client.put(f"/a/rca/items/{iid}/files/big.bin", content=b"0123456789")
    assert over.status_code == 413
    under = client.put(f"/a/rca/items/{iid}/files/ok.bin", content=b"012")
    assert under.status_code == 204
    assert client.get(f"/a/rca/items/{iid}/files/ok.bin").content == b"012"


def _quota_app(workspace_quota: int):
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=workspace_quota,
    )
    return app, spec


def test_upload_over_workspace_quota_returns_507():
    # #245: an upload that would push the workspace past its total quota is
    # rejected mid-stream with 507 + a structured body, and is not written.
    app, spec = _quota_app(workspace_quota=100)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    assert client.put(f"/a/rca/items/{iid}/files/a.bin", content=b"x" * 80).status_code == 204

    over = client.put(f"/a/rca/items/{iid}/files/b.bin", content=b"y" * 50)
    assert over.status_code == 507
    body = over.json()["detail"]
    assert body["error"] == "workspace_quota_exceeded"
    assert body["quota"] == 100
    # the rejected upload left nothing behind
    assert client.get(f"/a/rca/items/{iid}/files/b.bin").status_code == 404
    # exactly filling the remaining 20 bytes is allowed (boundary inclusive)
    assert client.put(f"/a/rca/items/{iid}/files/c.bin", content=b"z" * 20).status_code == 204


def test_overwrite_credits_old_bytes_so_same_size_replace_is_allowed():
    # #245: overwriting a file is a replace, not an add — re-uploading a file that
    # already fills the quota succeeds (its old bytes are credited back).
    app, spec = _quota_app(workspace_quota=100)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    assert client.put(f"/a/rca/items/{iid}/files/a.bin", content=b"x" * 100).status_code == 204
    # replace it with the same size — still 204, not 507
    assert client.put(f"/a/rca/items/{iid}/files/a.bin", content=b"y" * 100).status_code == 204
    # but a brand-new byte now overflows
    assert client.put(f"/a/rca/items/{iid}/files/b.bin", content=b"z").status_code == 507


def test_an_over_quota_workspace_can_still_shrink_a_file_through_the_upload_route():
    # #538: a workspace CAN sit over quota — the mirror writes the durable store
    # directly and is deliberately ungated. The whole point of gating on GROWTH
    # rather than on "already over" is that such a workspace can be tidied up;
    # if the route that IS the IDE save and the file-tree upload refuses a
    # shrink, that guarantee is worth nothing and only `delete` gets you out.
    spec = make_spec()
    store = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=store,
        runner=ScriptedAgentRunner([]),
        workspace_quota=100,
    )
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    # as the ungated mirror would leave it: over the quota, and — this is what
    # makes the two rules disagree — the excess is spread over MORE than the file
    # being shrunk, so `quota - (used - old)` is negative while the growth is not.
    asyncio.run(store.write(iid, "/other.bin", b"x" * 150))
    asyncio.run(store.write(iid, "/huge.bin", b"x" * 200))  # used = 350, quota = 100

    shrink = client.put(f"/a/rca/items/{iid}/files/huge.bin", content=b"y" * 50)
    assert shrink.status_code == 204
    assert client.get(f"/a/rca/items/{iid}/files/huge.bin").content == b"y" * 50
    # still over (200), so anything that grows the workspace is still refused
    assert client.put(f"/a/rca/items/{iid}/files/new.bin", content=b"z").status_code == 507


def test_renaming_a_folder_does_not_need_room_for_a_second_copy():
    # #538: `_transfer` copies the subtree then removes the source, so a RENAME —
    # which changes the workspace's size by zero — was asking for headroom the
    # size of the whole tree, and a mid-loop refusal left a half-copied folder
    # behind, leaving the user MORE over quota than before they asked.
    app, spec = _quota_app(workspace_quota=150)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    for name in ("one", "two", "three"):
        assert (
            client.put(f"/a/rca/items/{iid}/files/d/{name}.bin", content=b"x" * 30).status_code
            == 204
        )

    moved = client.post(f"/a/rca/items/{iid}/files/move", json={"from": "/d", "to": "/e"})
    assert moved.status_code == 204
    listing = {f["path"] for f in client.get(f"/a/rca/items/{iid}/files").json()}
    assert listing == {"/e/one.bin", "/e/two.bin", "/e/three.bin"}


def test_a_refused_folder_copy_leaves_nothing_behind():
    # The gate turned a loop that could not fail into one that can, so it has to
    # fail BEFORE it starts: a partially-copied folder is worse than a refusal.
    app, spec = _quota_app(workspace_quota=150)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    for name in ("one", "two", "three"):
        client.put(f"/a/rca/items/{iid}/files/d/{name}.bin", content=b"x" * 30)

    dup = client.post(f"/a/rca/items/{iid}/files/copy", json={"from": "/d", "to": "/e"})
    assert dup.status_code == 507
    listing = {f["path"] for f in client.get(f"/a/rca/items/{iid}/files").json()}
    assert listing == {"/d/one.bin", "/d/two.bin", "/d/three.bin"}


def test_copying_a_file_past_the_quota_returns_507():
    # #538: the quota lived in the upload endpoint's streaming loop, so every OTHER
    # way of growing a workspace — copy, move, the IDE save, a workflow — was free.
    # The gate now sits in the store facade all of them share.
    app, spec = _quota_app(workspace_quota=100)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    assert client.put(f"/a/rca/items/{iid}/files/a.bin", content=b"x" * 80).status_code == 204

    dup = client.post(f"/a/rca/items/{iid}/files/copy", json={"from": "/a.bin", "to": "/b.bin"})
    assert dup.status_code == 507
    assert dup.json()["detail"]["error"] == "workspace_quota_exceeded"
    assert client.get(f"/a/rca/items/{iid}/files/b.bin").status_code == 404


def test_workspace_quota_zero_disables_the_cap():
    # #245: quota of 0 means no per-workspace limit.
    app, spec = _quota_app(workspace_quota=0)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    assert client.put(f"/a/rca/items/{iid}/files/big.bin", content=b"0" * 10_000).status_code == 204


def test_usage_endpoint_reports_used_and_quota():
    # #245: the usage bar reads {used, quota}; used reflects writes and deletes.
    app, spec = _quota_app(workspace_quota=1000)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    base = client.get(f"/a/rca/items/{iid}/files/usage")
    assert base.status_code == 200
    assert base.json() == {"used": 0, "quota": 1000}

    client.put(f"/a/rca/items/{iid}/files/a.bin", content=b"x" * 300)
    assert client.get(f"/a/rca/items/{iid}/files/usage").json() == {"used": 300, "quota": 1000}

    client.delete(f"/a/rca/items/{iid}/files/a.bin")
    assert client.get(f"/a/rca/items/{iid}/files/usage").json() == {"used": 0, "quota": 1000}


def test_usage_endpoint_quota_zero_surfaces_unlimited():
    # #245: quota 0 → the FE hides the bar; the endpoint reports it plainly.
    app, spec = _quota_app(workspace_quota=0)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    assert client.get(f"/a/rca/items/{iid}/files/usage").json() == {"used": 0, "quota": 0}


def test_list_files_reports_sizes_without_reading_file_contents():
    # #362: GET /files must build the tree from cheap metadata (walk stat / the
    # record's inline size), NEVER by reading each file's bytes. A regression to
    # the old per-file read would trip the `read` spy below.
    spec = make_spec()
    fs = SpecstarFileStore(spec)
    reads: list[str] = []
    orig_read = fs.read

    async def _spy_read(workspace_id: str, path: str) -> bytes:
        reads.append(path)
        return await orig_read(workspace_id, path)

    fs.read = _spy_read  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=fs,
        runner=ScriptedAgentRunner([]),
    )
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    client.put(f"/a/rca/items/{iid}/files/a.txt", content=b"hello")
    client.put(f"/a/rca/items/{iid}/files/sub/b.txt", content=b"world!")
    reads.clear()

    resp = client.get(f"/a/rca/items/{iid}/files")
    assert resp.status_code == 200
    by_path = {e["path"]: e for e in resp.json()}
    assert by_path["/a.txt"]["size"] == 5
    assert by_path["/sub/b.txt"]["size"] == 6
    # response shape is unchanged so the FE needn't move (#362 backend-only)
    assert set(by_path["/a.txt"]) == {"path", "size", "read_only"}
    assert by_path["/a.txt"]["read_only"] is False
    assert reads == []  # listing read zero file contents


def test_refresh_files_is_ok_when_cold(harness: Harness):
    # No sandbox up for this investigation → flush is a no-op, endpoint still OK.
    resp = harness.client.post(harness.wpath("/files/refresh"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_delete_file_removes_it(harness: Harness):
    harness.client.put(harness.wpath("/files/a.txt"), content=b"bye")
    resp = harness.client.delete(harness.wpath("/files/a.txt"))
    assert resp.status_code == 204
    assert harness.client.get(harness.wpath("/files/a.txt")).status_code == 404


def test_delete_missing_file_returns_404(harness: Harness):
    resp = harness.client.delete(harness.wpath("/files/nope.txt"))
    assert resp.status_code == 404


async def test_delete_nested_path(harness: Harness):
    harness.client.put(harness.wpath("/files/sub/x.csv"), content=b"1,2")
    resp = harness.client.delete(harness.wpath("/files/sub/x.csv"))
    assert resp.status_code == 204
    assert harness.client.get(harness.wpath("/files/sub/x.csv")).status_code == 404


async def test_move_renames_file(harness: Harness):
    harness.client.put(harness.wpath("/files/brief.md"), content=b"# hi")
    resp = harness.client.post(
        harness.wpath("/files/move"),
        json={"from": "/brief.md", "to": "/overview.md"},
    )
    assert resp.status_code == 204
    # old gone, new present with same content
    assert harness.client.get(harness.wpath("/files/brief.md")).status_code == 404
    got = harness.client.get(harness.wpath("/files/overview.md"))
    assert got.status_code == 200
    assert got.content == b"# hi"


async def test_move_into_subfolder(harness: Harness):
    harness.client.put(harness.wpath("/files/x.csv"), content=b"a,b")
    resp = harness.client.post(
        harness.wpath("/files/move"),
        json={"from": "/x.csv", "to": "/data/x.csv"},
    )
    assert resp.status_code == 204
    assert harness.client.get(harness.wpath("/files/data/x.csv")).content == b"a,b"


def test_move_missing_source_returns_404(harness: Harness):
    resp = harness.client.post(
        harness.wpath("/files/move"),
        json={"from": "/ghost.md", "to": "/x.md"},
    )
    assert resp.status_code == 404


async def test_copy_duplicates_file(harness: Harness):
    harness.client.put(harness.wpath("/files/a.md"), content=b"# hi")
    resp = harness.client.post(
        harness.wpath("/files/copy"),
        json={"from": "/a.md", "to": "/b.md"},
    )
    assert resp.status_code == 204
    # both present, same content
    assert harness.client.get(harness.wpath("/files/a.md")).content == b"# hi"
    assert harness.client.get(harness.wpath("/files/b.md")).content == b"# hi"


def test_copy_missing_source_returns_404(harness: Harness):
    resp = harness.client.post(
        harness.wpath("/files/copy"),
        json={"from": "/ghost.md", "to": "/x.md"},
    )
    assert resp.status_code == 404


async def test_copy_rejects_overwrite(harness: Harness):
    harness.client.put(harness.wpath("/files/a.md"), content=b"a")
    harness.client.put(harness.wpath("/files/b.md"), content=b"b")
    resp = harness.client.post(
        harness.wpath("/files/copy"),
        json={"from": "/a.md", "to": "/b.md"},
    )
    assert resp.status_code == 409


async def test_move_rejects_overwrite_of_existing_target(harness: Harness):
    harness.client.put(harness.wpath("/files/a.md"), content=b"a")
    harness.client.put(harness.wpath("/files/b.md"), content=b"b")
    resp = harness.client.post(
        harness.wpath("/files/move"),
        json={"from": "/a.md", "to": "/b.md"},
    )
    assert resp.status_code == 409
    # both still intact
    assert harness.client.get(harness.wpath("/files/a.md")).content == b"a"
    assert harness.client.get(harness.wpath("/files/b.md")).content == b"b"


def test_a_refused_replace_changes_no_files_at_all():
    # #538 follow-up (M5): the gate turned a loop that could not fail into one
    # that can, and a search/replace that stops on file 37 of 100 leaves the
    # workspace in a state the user never asked for and can't see — the response
    # doesn't even carry how many were rewritten. It has to fail before it
    # starts, like the folder copy does.
    app, spec = _quota_app(workspace_quota=200)
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    for n in ("a", "b", "c"):
        client.put(f"/a/rca/items/{iid}/files/{n}.txt", content=b"xx")  # 6 bytes total

    # each "xx" -> 100 bytes, so the three together need 300 against a 200 cap;
    # per-file gating writes the first and refuses the second.
    grew = client.post(
        f"/a/rca/items/{iid}/replace",
        json={"query": "xx", "replacement": "y" * 100, "regex": False},
    )
    assert grew.status_code == 507
    for n in ("a", "b", "c"):
        assert client.get(f"/a/rca/items/{iid}/files/{n}.txt").content == b"xx"


class _BusySandbox(MockSandbox):
    """A sandbox that is reachable but not answering yet — what a hosted host
    reports while a container is still coming up."""

    busy = False

    async def walk(self, handle, root):  # type: ignore[no-untyped-def]
        if self.busy:
            raise SandboxBusy("still starting up")
        return await super().walk(handle, root)

    async def exists(self, handle, path):  # type: ignore[no-untyped-def]
        if self.busy:
            raise SandboxBusy("still starting up")
        return await super().exists(handle, path)


def test_a_busy_sandbox_is_a_503_not_a_500():
    """#538: `SandboxBusy` propagates on purpose (#366 — failing beats writing
    into a second sandbox), but nothing mapped it to a status, so "your
    workspace is still starting" reached the user as an internal error. It is
    transient and worth retrying, and saying so is the difference between a
    spinner and a bug report."""
    spec = make_spec()
    sandbox = _BusySandbox()
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    client.post(f"/a/rca/items/{iid}/exec", json={"cmd": ["echo", "hi"]})  # wake it

    sandbox.busy = True
    resp = client.get(f"/a/rca/items/{iid}/files")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "sandbox_busy"
    assert resp.headers.get("retry-after")


# ── a missing file is a 404, not an opaque 500 (#588) ──────────────────────


def test_a_file_layer_miss_is_a_404_not_a_500():
    """`create_app` handled `WorkspaceFull` / `SandboxBusy` / `SandboxNotFound`
    and nothing else, so a `FileNotFound` escaping any hand-written route became
    an unhandled 500 — the reason a failed move could only be diagnosed by
    reading server logs. It is a missing file; say so."""
    spec = make_spec()
    fs = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=fs,
        runner=ScriptedAgentRunner([]),
    )
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    assert client.put(f"/a/rca/items/{iid}/files/folder/a.txt", content=b"hi").status_code == 204

    # Stand in for the source vanishing between the read and the delete — a
    # sandbox reaped or rebuilt mid-move sends the two to different stores.
    real = fs.delete

    async def _gone(workspace_id: str, path: str) -> None:
        if path.endswith("folder/a.txt"):
            raise FileNotFound(path)
        await real(workspace_id, path)

    fs.delete = _gone  # ty: ignore[invalid-assignment]
    r = client.post(
        f"/a/rca/items/{iid}/files/move", json={"from": "/folder/a.txt", "to": "/a.txt"}
    )
    fs.delete = real  # ty: ignore[invalid-assignment]

    assert r.status_code == 404

    # …and the failed move left the workspace exactly as it was.
    paths = {e["path"] for e in client.get(f"/a/rca/items/{iid}/files").json()}
    assert paths == {"/folder/a.txt"}
