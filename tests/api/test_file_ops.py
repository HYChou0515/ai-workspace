"""DELETE /files/{path} + POST /files/move — backs the file-tree
right-click menu (Delete / Rename / Move).
"""

from __future__ import annotations

from .conftest import Harness


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
