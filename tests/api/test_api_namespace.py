"""#177 — the backend lives entirely under /api; the SPA owns the rest.

Two guarantees, tested against the *raw* TestClient (no auto-/api rewrite — this
file asserts the bare-URL behaviour a browser actually sees on a hard refresh):

1. Structural guardrail: every registered backend route — including the docs /
   openapi / redoc routes — sits under ``/api``. Only the SPA static mount owns
   the root. A new route added without the prefix trips this immediately.
2. Regression: refreshing a client-side route whose path mirrors a KB REST path
   (``/kb/chats/{id}``, ``/kb/collections`` …) serves index.html, not JSON. That
   collision (FE route ↔ API route sharing ``/kb``) was the actual #177 bug.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient  # raw — NOT the auto-/api wrapper
from starlette.routing import Mount

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


def _app(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><div id=root>RCA SPA</div>")
    spec = make_spec(default_user="u")
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        spa_dist=tmp_path,
    )


def test_all_backend_routes_under_api(tmp_path: Path):
    """Every route except the SPA mount is under /api (docs/openapi included)."""
    app = _app(tmp_path)
    offenders = [
        getattr(r, "path", "")
        for r in app.routes
        if not isinstance(r, Mount)
        and getattr(r, "name", None) != "spa"
        and not getattr(r, "path", "").startswith("/api")
    ]
    assert offenders == [], f"routes not under /api: {offenders}"


def test_browser_refresh_serves_spa(tmp_path: Path):
    """The KB client routes that mirror REST paths must fall back to the SPA on a
    hard refresh — index.html, not the API's JSON."""
    client = TestClient(_app(tmp_path))
    for path in (
        "/kb/chats/conversation:abc",
        "/kb/chats",
        "/kb/collections",
        "/kb/collections/collection:1/documents",
        "/kb/collections/collection:1/wiki",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code}"
        assert "text/html" in resp.headers.get("content-type", ""), (
            f"{path} served {resp.headers.get('content-type')}, expected SPA html"
        )
        assert "RCA SPA" in resp.text, f"{path} did not serve index.html"
