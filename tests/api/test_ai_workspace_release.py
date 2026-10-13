"""Publishing the helper (docs/plan-outside-helper.md §3.7.2): the
operator drops `ai-workspace.json` + the zip it names into
`server.ai_workspace_dir`; the backend serves exactly those, read on every
request, so a new release needs no redeploy."""

from __future__ import annotations

import hashlib
import json

from tests.api.test_outside_answers import _app


def _publish(folder, version="0.3.0", **over):
    zip_bytes = f"zip of {version}".encode()
    (folder / f"ai-workspace-{version}.zip").write_bytes(zip_bytes)
    manifest = {
        "version": version,
        "min_version": "0.2.0",
        "file": f"ai-workspace-{version}.zip",
        "sha256": hashlib.sha256(zip_bytes).hexdigest(),
        **over,
    }
    (folder / "ai-workspace.json").write_text(json.dumps(manifest))
    return manifest, zip_bytes


def test_a_published_release_is_described_and_downloadable(tmp_path):
    manifest, zip_bytes = _publish(tmp_path)
    client, _spec = _app(ai_workspace_dir=str(tmp_path))

    r = client.get("/ai-workspace/manifest")

    assert r.status_code == 200, r.text
    assert r.json() == {**manifest, "size": len(zip_bytes)}
    d = client.get(f"/ai-workspace/download/{manifest['file']}")
    assert d.status_code == 200
    assert d.content == zip_bytes
    assert manifest["file"] in d.headers["content-disposition"]


# §3.7.2: "沒設或資料夾空 → 尚未發布".
def test_with_nothing_published_there_is_no_release(tmp_path):
    for client, _spec in (_app(), _app(ai_workspace_dir=str(tmp_path))):
        assert client.get("/ai-workspace/manifest").status_code == 404
        assert client.get("/ai-workspace/download/ai-workspace-0.3.0.zip").status_code == 404


# "只給版本資訊裡列的那個檔,不能拿來讀資料夾裡別的東西".
def test_only_the_file_the_manifest_names_is_served(tmp_path):
    _publish(tmp_path)
    (tmp_path / "notes.txt").write_text("operator's notes")
    (tmp_path / "ai-workspace-0.2.0.zip").write_bytes(b"an old release")
    client, _spec = _app(ai_workspace_dir=str(tmp_path))

    for name in (
        "notes.txt",
        "ai-workspace-0.2.0.zip",
        "ai-workspace.json",
        "..%2F..%2Fetc%2Fpasswd",
    ):
        assert client.get(f"/ai-workspace/download/{name}").status_code == 404, name


# A manifest that names a path (an operator's slip, or worse) publishes
# nothing rather than opening the disk beyond the folder.
def test_a_manifest_naming_a_path_publishes_nothing(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.zip").write_bytes(b"x")
    for bad in ("sub/x.zip", "../x.zip", "sub\\x.zip", ""):
        _publish(tmp_path, file=bad)
        client, _spec = _app(ai_workspace_dir=str(tmp_path))
        assert client.get("/ai-workspace/manifest").status_code == 404, bad
        assert client.get("/ai-workspace/download/x.zip").status_code == 404, bad


def test_a_new_release_is_served_without_a_restart(tmp_path):
    _publish(tmp_path, "0.3.0")
    client, _spec = _app(ai_workspace_dir=str(tmp_path))
    assert client.get("/ai-workspace/manifest").json()["version"] == "0.3.0"

    _publish(tmp_path, "0.4.0")

    assert client.get("/ai-workspace/manifest").json()["version"] == "0.4.0"
    assert client.get("/ai-workspace/download/ai-workspace-0.4.0.zip").status_code == 200


def test_a_manifest_that_names_a_missing_file_is_not_a_release(tmp_path):
    manifest, _ = _publish(tmp_path)
    (tmp_path / manifest["file"]).unlink()
    client, _spec = _app(ai_workspace_dir=str(tmp_path))

    assert client.get("/ai-workspace/manifest").status_code == 404


def test_a_manifest_that_is_not_json_is_not_a_release(tmp_path):
    (tmp_path / "ai-workspace.json").write_text("{")
    client, _spec = _app(ai_workspace_dir=str(tmp_path))

    assert client.get("/ai-workspace/manifest").status_code == 404
