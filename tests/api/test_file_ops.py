"""DELETE /files/{path} + POST /files/move — backs the file-tree
right-click menu (Delete / Rename / Move).
"""

from __future__ import annotations

from .conftest import Harness


async def test_delete_file_removes_it(harness: Harness):
    harness.client.put("/investigations/ws-d/files/a.txt", content=b"bye")
    resp = harness.client.delete("/investigations/ws-d/files/a.txt")
    assert resp.status_code == 204
    assert harness.client.get("/investigations/ws-d/files/a.txt").status_code == 404


def test_delete_missing_file_returns_404(harness: Harness):
    resp = harness.client.delete("/investigations/ws-d/files/nope.txt")
    assert resp.status_code == 404


async def test_delete_nested_path(harness: Harness):
    harness.client.put("/investigations/ws-d/files/sub/x.csv", content=b"1,2")
    resp = harness.client.delete("/investigations/ws-d/files/sub/x.csv")
    assert resp.status_code == 204
    assert harness.client.get("/investigations/ws-d/files/sub/x.csv").status_code == 404


async def test_move_renames_file(harness: Harness):
    harness.client.put("/investigations/ws-m/files/brief.md", content=b"# hi")
    resp = harness.client.post(
        "/investigations/ws-m/files/move",
        json={"from": "/brief.md", "to": "/overview.md"},
    )
    assert resp.status_code == 204
    # old gone, new present with same content
    assert harness.client.get("/investigations/ws-m/files/brief.md").status_code == 404
    got = harness.client.get("/investigations/ws-m/files/overview.md")
    assert got.status_code == 200
    assert got.content == b"# hi"


async def test_move_into_subfolder(harness: Harness):
    harness.client.put("/investigations/ws-m/files/x.csv", content=b"a,b")
    resp = harness.client.post(
        "/investigations/ws-m/files/move",
        json={"from": "/x.csv", "to": "/data/x.csv"},
    )
    assert resp.status_code == 204
    assert harness.client.get("/investigations/ws-m/files/data/x.csv").content == b"a,b"


def test_move_missing_source_returns_404(harness: Harness):
    resp = harness.client.post(
        "/investigations/ws-m/files/move",
        json={"from": "/ghost.md", "to": "/x.md"},
    )
    assert resp.status_code == 404


async def test_copy_duplicates_file(harness: Harness):
    harness.client.put("/investigations/ws-c/files/a.md", content=b"# hi")
    resp = harness.client.post(
        "/investigations/ws-c/files/copy",
        json={"from": "/a.md", "to": "/b.md"},
    )
    assert resp.status_code == 204
    # both present, same content
    assert harness.client.get("/investigations/ws-c/files/a.md").content == b"# hi"
    assert harness.client.get("/investigations/ws-c/files/b.md").content == b"# hi"


def test_copy_missing_source_returns_404(harness: Harness):
    resp = harness.client.post(
        "/investigations/ws-c/files/copy",
        json={"from": "/ghost.md", "to": "/x.md"},
    )
    assert resp.status_code == 404


async def test_copy_rejects_overwrite(harness: Harness):
    harness.client.put("/investigations/ws-c/files/a.md", content=b"a")
    harness.client.put("/investigations/ws-c/files/b.md", content=b"b")
    resp = harness.client.post(
        "/investigations/ws-c/files/copy",
        json={"from": "/a.md", "to": "/b.md"},
    )
    assert resp.status_code == 409


async def test_move_rejects_overwrite_of_existing_target(harness: Harness):
    harness.client.put("/investigations/ws-m/files/a.md", content=b"a")
    harness.client.put("/investigations/ws-m/files/b.md", content=b"b")
    resp = harness.client.post(
        "/investigations/ws-m/files/move",
        json={"from": "/a.md", "to": "/b.md"},
    )
    assert resp.status_code == 409
    # both still intact
    assert harness.client.get("/investigations/ws-m/files/a.md").content == b"a"
    assert harness.client.get("/investigations/ws-m/files/b.md").content == b"b"
