"""Issue #247: workspace folder/root raw-file download — two-step prepare→stream.

A plain ZIP of the workspace files under a path prefix (`prefix=""` = the whole
workspace), entries rooted at the selected folder. The reserved `.readonly/`
agent-diff snapshots are not user content and are excluded.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

from .conftest import Harness, register_rca_item


def _zip(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def test_folder_download_zips_workspace_files_relative_to_prefix(harness: Harness):
    h = harness
    h.client.put(h.wpath("/files/data/a.csv"), content=b"1,2")
    h.client.put(h.wpath("/files/data/sub/b.txt"), content=b"beta")
    h.client.put(h.wpath("/files/top.md"), content=b"top")  # sibling, excluded

    prep = h.client.post(h.wpath("/files/download/prepare?prefix=/data"))
    assert prep.status_code == 200, prep.text
    body = prep.json()
    assert body["download_id"]
    assert body["filename"] == "data.zip"
    assert body["size"] > 0

    dl = h.client.get(h.wpath(f"/files/download/{body['download_id']}?prefix=/data"))
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/zip")
    assert "data.zip" in dl.headers["content-disposition"]

    zf = _zip(dl.content)
    assert set(zf.namelist()) == {"a.csv", "sub/b.txt"}
    assert zf.read("a.csv") == b"1,2"
    assert zf.read("sub/b.txt") == b"beta"


def test_root_download_zips_whole_workspace_named_after_item(harness: Harness):
    h = harness
    h.client.put(h.wpath("/files/a.txt"), content=b"A")
    h.client.put(h.wpath("/files/d/b.txt"), content=b"B")

    prep = h.client.post(h.wpath("/files/download/prepare")).json()
    assert prep["filename"] == "t.zip"  # root → the item title ("t")
    dl = h.client.get(h.wpath(f"/files/download/{prep['download_id']}"))
    assert set(_zip(dl.content).namelist()) == {"a.txt", "d/b.txt"}  # full tree from root


def test_readonly_snapshots_are_excluded(harness: Harness):
    h = harness
    h.client.put(h.wpath("/files/real.txt"), content=b"real")
    # `.readonly/` is write-rejected by the route, so seed it through the store.
    asyncio.run(h.filestore.write(h.iid, "/.readonly/snap.txt", b"snapshot"))

    prep = h.client.post(h.wpath("/files/download/prepare")).json()
    dl = h.client.get(h.wpath(f"/files/download/{prep['download_id']}"))
    assert set(_zip(dl.content).namelist()) == {"real.txt"}  # snapshot excluded


def test_untitled_item_root_download_falls_back_to_workspace_name(harness: Harness):
    # An item with a blank title → the zip is named "workspace.zip", not ".zip".
    iid = register_rca_item(harness.spec, title="")
    base = f"/a/rca/items/{iid}"
    harness.client.put(f"{base}/files/x.txt", content=b"x")

    prep = harness.client.post(f"{base}/files/download/prepare").json()
    assert prep["filename"] == "workspace.zip"


def test_streaming_consumes_the_prepared_download(harness: Harness):
    h = harness
    h.client.put(h.wpath("/files/a.txt"), content=b"a")
    did = h.client.post(h.wpath("/files/download/prepare")).json()["download_id"]
    assert h.client.get(h.wpath(f"/files/download/{did}")).status_code == 200
    # deleted after the first send — a re-fetch 404s.
    assert h.client.get(h.wpath(f"/files/download/{did}")).status_code == 404


def test_stream_rejects_malformed_or_unknown_download_id(harness: Harness):
    h = harness
    assert h.client.get(h.wpath("/files/download/not-a-token")).status_code == 404
    assert h.client.get(h.wpath(f"/files/download/{'0' * 32}")).status_code == 404


def test_empty_subtree_yields_an_empty_zip(harness: Harness):
    h = harness
    h.client.put(h.wpath("/files/a.txt"), content=b"a")
    prep = h.client.post(h.wpath("/files/download/prepare?prefix=/nope")).json()
    dl = h.client.get(h.wpath(f"/files/download/{prep['download_id']}?prefix=/nope"))
    assert _zip(dl.content).namelist() == []


def test_download_unknown_item_is_404(harness: Harness):
    # the slug→item guard (#95) rejects a bad item on both steps
    assert harness.client.post("/a/rca/items/nope/files/download/prepare").status_code == 404
    assert harness.client.get(f"/a/rca/items/nope/files/download/{'0' * 32}").status_code == 404
