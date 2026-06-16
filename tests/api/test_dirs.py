"""Directory endpoints — honest folders (no .keep), backing the file-tree
create-folder / rename / delete / move actions.
"""

from __future__ import annotations

from .conftest import Harness


def _dirs(h: Harness) -> list[str]:
    resp = h.client.get(h.wpath("/dirs"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _files(h: Harness) -> list[str]:
    return [f["path"] for f in h.client.get(h.wpath("/files")).json()]


def test_mkdir_creates_empty_dir_with_no_files(harness: Harness):
    resp = harness.client.post(harness.wpath("/files/mkdir"), json={"path": "/notes"})
    assert resp.status_code == 204, resp.text
    assert "/notes" in _dirs(harness)
    assert _files(harness) == []  # no .keep placeholder


def test_mkdir_over_existing_file_conflicts(harness: Harness):
    harness.client.put(harness.wpath("/files/d"), content=b"x")
    resp = harness.client.post(harness.wpath("/files/mkdir"), json={"path": "/d"})
    assert resp.status_code == 409


def test_write_exposes_ancestor_dirs(harness: Harness):
    harness.client.put(harness.wpath("/files/data/raw/x.csv"), content=b"1")
    dirs = _dirs(harness)
    assert "/data" in dirs and "/data/raw" in dirs


def test_deleting_last_file_keeps_the_dir(harness: Harness):
    harness.client.put(harness.wpath("/files/d/a.txt"), content=b"a")
    assert harness.client.delete(harness.wpath("/files/d/a.txt")).status_code == 204
    assert "/d" in _dirs(harness)  # empty folder survives


def test_delete_folder_removes_subtree(harness: Harness):
    harness.client.put(harness.wpath("/files/d/a.txt"), content=b"a")
    harness.client.put(harness.wpath("/files/d/sub/b.txt"), content=b"b")
    harness.client.post(harness.wpath("/files/mkdir"), json={"path": "/d/empty"})
    resp = harness.client.delete(harness.wpath("/files/d"))
    assert resp.status_code == 204
    assert "/d" not in _dirs(harness)
    assert "/d/sub" not in _dirs(harness)
    assert _files(harness) == []


def test_move_folder_relocates_the_subtree(harness: Harness):
    harness.client.put(harness.wpath("/files/src/a.txt"), content=b"a")
    harness.client.put(harness.wpath("/files/src/sub/b.txt"), content=b"b")
    resp = harness.client.post(harness.wpath("/files/move"), json={"from": "/src", "to": "/dst"})
    assert resp.status_code == 204, resp.text
    assert harness.client.get(harness.wpath("/files/dst/a.txt")).content == b"a"
    assert harness.client.get(harness.wpath("/files/dst/sub/b.txt")).content == b"b"
    assert harness.client.get(harness.wpath("/files/src/a.txt")).status_code == 404
    assert "/src" not in _dirs(harness)
    assert "/dst" in _dirs(harness)


def test_copy_folder_duplicates_the_subtree(harness: Harness):
    harness.client.put(harness.wpath("/files/src/a.txt"), content=b"a")
    resp = harness.client.post(harness.wpath("/files/copy"), json={"from": "/src", "to": "/dup"})
    assert resp.status_code == 204, resp.text
    assert harness.client.get(harness.wpath("/files/dup/a.txt")).content == b"a"
    assert harness.client.get(harness.wpath("/files/src/a.txt")).content == b"a"  # original kept


def test_move_folder_onto_existing_target_conflicts(harness: Harness):
    harness.client.put(harness.wpath("/files/src/a.txt"), content=b"a")
    harness.client.post(harness.wpath("/files/mkdir"), json={"path": "/dst"})
    resp = harness.client.post(harness.wpath("/files/move"), json={"from": "/src", "to": "/dst"})
    assert resp.status_code == 409


def test_move_folder_into_itself_rejected(harness: Harness):
    harness.client.put(harness.wpath("/files/src/a.txt"), content=b"a")
    resp = harness.client.post(
        harness.wpath("/files/move"), json={"from": "/src", "to": "/src/inner"}
    )
    assert resp.status_code == 400
