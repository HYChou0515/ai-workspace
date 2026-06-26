"""DELETE /files/{path} + POST /files/move — backs the file-tree
right-click menu (Delete / Rename / Move).
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

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
