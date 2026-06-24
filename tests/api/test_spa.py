"""SPA history fallback — refreshing a client-side route like
/a/{slug}/items/{id} must serve index.html, not a 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<!doctype html><div id=root>RCA SPA</div>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        spa_dist=tmp_path,
    )
    return TestClient(app)


def test_root_serves_index(tmp_path: Path):
    resp = _client(tmp_path).get("/")
    assert resp.status_code == 200
    assert "RCA SPA" in resp.text


def test_deep_client_route_falls_back_to_index(tmp_path: Path):
    """Refreshing a client route boots the SPA (index.html), not a 404."""
    resp = _client(tmp_path).get("/a/rca/items/abc-123")
    assert resp.status_code == 200
    assert "RCA SPA" in resp.text


def test_real_asset_is_served_directly(tmp_path: Path):
    resp = _client(tmp_path).get("/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_index_is_served_no_cache(tmp_path: Path):
    """index.html must always be revalidated so a rebuild's new hashed-asset
    references are picked up — both at `/` and via the history fallback."""
    client = _client(tmp_path)
    assert client.get("/").headers.get("cache-control") == "no-cache"
    fallback = client.get("/a/rca/items/abc-123")
    assert fallback.headers.get("cache-control") == "no-cache"


def test_hashed_asset_is_not_no_cache(tmp_path: Path):
    """Real (hashed) assets stay cacheable — only index.html is no-cache."""
    resp = _client(tmp_path).get("/assets/app.js")
    assert resp.headers.get("cache-control") != "no-cache"


def test_unknown_api_route_still_404s_json(tmp_path: Path):
    """An /api/* path that matches NO route falls through to the SPA mount, but
    the `api/` guard makes it 404 rather than serving index.html (#177) — an API
    miss must stay an API miss, never a masked SPA page."""
    resp = _client(tmp_path).get("/api/__no_such_route__")
    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")


def test_non_get_to_spa_path_is_not_rewritten(tmp_path: Path):
    """Only 404s fall back to index.html — other static errors (e.g. a
    405 for a non-GET method) propagate unchanged."""
    resp = _client(tmp_path).post("/a/rca/items/abc")
    assert resp.status_code == 405
