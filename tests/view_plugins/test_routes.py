"""Serving runtime view plugins (#847/#848 PR1 P3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from workspace_app.api.view_plugin_routes import register_view_plugin_routes
from workspace_app.view_plugins import discover_view_plugins


def _plugin(root: Path, name: str, kinds: list[str]) -> None:
    d = root / name
    (d / "web" / "chunks").mkdir(parents=True)
    (d / "web" / "index.js").write_text(f"export const who = {name!r};\n")
    (d / "web" / "chunks" / "a.js").write_text("export const a = 1;\n")
    (d / "web" / "style.css").write_text("body{}")
    (d / "plugin.json").write_text(json.dumps({"name": name, "sdk": "1", "kinds": kinds}))
    (d / "secret.txt").write_text("outside web/")


def _client(tmp_path: Path, *, user: str | None = "alice") -> TestClient:
    root = tmp_path / "plugins"
    _plugin(root, "chart", ["chart"])
    _plugin(root, "wafer", ["wafer-map", "wafer-stack"])
    plugins = discover_view_plugins(root)

    def get_user_id() -> str:
        if user is None:
            raise HTTPException(status_code=401, detail="sign in")
        return user

    app = FastAPI()
    register_view_plugin_routes(app, get_plugins=lambda: plugins, get_user_id=get_user_id)
    return TestClient(app)


def test_lists_the_plugins(tmp_path: Path):
    resp = _client(tmp_path).get("/view-plugins")
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "name": "chart",
            "sdk": "1",
            "kinds": ["chart"],
            "entry_url": "/view-plugins/chart/index.js",
        },
        {
            "name": "wafer",
            "sdk": "1",
            "kinds": ["wafer-map", "wafer-stack"],
            "entry_url": "/view-plugins/wafer/index.js",
        },
    ]


def test_the_list_needs_a_signed_in_user(tmp_path: Path):
    assert _client(tmp_path, user=None).get("/view-plugins").status_code == 401


def test_serves_the_entry_as_javascript_revalidated(tmp_path: Path):
    resp = _client(tmp_path).get("/view-plugins/chart/index.js")
    assert resp.status_code == 200
    assert "who = 'chart'" in resp.text
    assert resp.headers["content-type"].split(";")[0] in {
        "text/javascript",
        "application/javascript",
    }
    # A plugin's file names are fixed, so an operator's reinstall must reach the
    # next page load.
    assert resp.headers["cache-control"] == "no-cache"


def test_serves_nested_files(tmp_path: Path):
    client = _client(tmp_path)
    assert client.get("/view-plugins/chart/chunks/a.js").text == "export const a = 1;\n"
    assert client.get("/view-plugins/chart/style.css").status_code == 200


def test_files_need_a_signed_in_user(tmp_path: Path):
    assert _client(tmp_path, user=None).get("/view-plugins/chart/index.js").status_code == 401


def test_unknown_plugin_or_file_is_404(tmp_path: Path):
    client = _client(tmp_path)
    assert client.get("/view-plugins/nope/index.js").status_code == 404
    assert client.get("/view-plugins/chart/missing.js").status_code == 404
    assert client.get("/view-plugins/chart/chunks").status_code == 404  # a dir is not a file


@pytest.mark.parametrize(
    "path",
    [
        "/view-plugins/chart/..%2Fsecret.txt",
        "/view-plugins/chart/..%2Fplugin.json",
        "/view-plugins/chart/chunks/..%2F..%2Fsecret.txt",
        "/view-plugins/chart/%2E%2E/secret.txt",
        "/view-plugins/chart/..%2F..%2Fwafer%2Fweb%2Findex.js",
        "/view-plugins/chart/%2Fetc%2Fpasswd",
    ],
)
def test_traversal_out_of_web_is_refused(tmp_path: Path, path: str):
    resp = _client(tmp_path).get(path)
    assert resp.status_code == 404
    assert "outside web/" not in resp.text


def test_a_symlink_out_of_web_is_refused(tmp_path: Path):
    client = _client(tmp_path)
    web = tmp_path / "plugins" / "chart" / "web"
    (web / "link.txt").symlink_to(tmp_path / "plugins" / "chart" / "secret.txt")
    assert client.get("/view-plugins/chart/link.txt").status_code == 404


def test_create_app_serves_them_under_api(tmp_path: Path):
    """Through the real composition: `/api/view-plugins`, ahead of the SPA
    fallback (an unmatched `/api/*` would 404 as JSON, not serve index.html)."""
    from workspace_app.api import create_app
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    root = tmp_path / "plugins"
    _plugin(root, "chart", ["chart"])
    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        view_plugins=discover_view_plugins(root),
    )
    client = TestClient(app)
    assert [p["name"] for p in client.get("/api/view-plugins").json()] == ["chart"]
    assert "who = 'chart'" in client.get("/api/view-plugins/chart/index.js").text
