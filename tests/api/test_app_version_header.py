"""Version-skew handshake: every backend response carries `X-App-Version`.

A cached OLD web bundle talking to a NEW api broke chats after the
v2026.07.23 deploy (the #601 event-shape change). The FE bakes its own build
version and compares it against this header at its one fetch chokepoint; a
mismatch triggers a reload at a safe moment. The header must be on EVERY
response — including errors — because the FE may learn of the skew from any
call it happens to make first.
"""

from __future__ import annotations

from importlib.metadata import version

from workspace_app.api import create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client() -> TestClient:
    app = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=None,  # ty: ignore[invalid-argument-type] — no turn is driven here
    )
    return TestClient(app)


def test_every_api_response_carries_the_app_version():
    client = _client()
    resp = client.get("/api/apps")
    assert resp.headers.get("X-App-Version") == version("workspace-app")


def test_even_an_error_response_carries_the_app_version():
    """The FE may learn of the skew from ANY response, a 404 included."""
    client = _client()
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.headers.get("X-App-Version") == version("workspace-app")
