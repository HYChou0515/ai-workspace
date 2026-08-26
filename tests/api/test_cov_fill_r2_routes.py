"""Cov-fill for the post-#54 split route modules.

Two reachable branches that the 100% gate flagged as missing once their only
prior coverage came from flaky LLM-path integration tests:

  - capability_routes.py: the invalid/forged ``X-Workflow-Token`` → 401 branch
    in BOTH ``capability_ingest`` and ``capability_context_card``
    (``claims is None`` ⇒ HTTPException(401)).
  - item_routes.py: ``create_app_item`` wraps a ``msgspec.ValidationError`` from
    ``msgspec.convert`` into a 422 (here a bad enum value for ``severity``).

(A third case lived here — ``get_app_manifest`` inlining a shipped ``icon.svg``.
A file icon is served from ``GET /apps/{slug}/icon`` now, so the manifest inlines
nothing; the behaviour it stood in for is tested through that route in
``test_apps_routes.py``.)

All wiring is deterministic: ScriptedAgentRunner / MockSandbox / MemoryFileStore /
HashEmbedder via ``create_app`` — no real LLM / docker.
"""

from __future__ import annotations

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _app():
    """A full ``create_app`` — wires the workflow_credentials broker + executor the
    capability routes depend on, plus the meta + item routes."""
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="ok"), RunDone()]),
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
    )
    return app, spec


# ── capability_routes.py: forged token → 401 ─────────────────────────


def test_capability_ingest_forged_token_is_401():
    """``claims is None`` (an unresolvable token) ⇒ 401, not the user fallback."""
    app, _spec = _app()
    client = TestClient(app)
    item_id = client.post("/a/rca/items", json={"title": "Oven drift"}).json()["resource_id"]
    r = client.post(
        f"/a/rca/items/{item_id}/capabilities/ingest",
        json={"collection": "c", "path": "a.md"},
        headers={"X-Workflow-Token": "forged"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid or expired workflow token"


def test_capability_context_card_forged_token_is_401():
    """Same auth guard on the context-card capability."""
    app, _spec = _app()
    client = TestClient(app)
    item_id = client.post("/a/rca/items", json={"title": "Oven drift"}).json()["resource_id"]
    r = client.post(
        f"/a/rca/items/{item_id}/capabilities/context-card",
        json={"collection": "c", "keys": ["x"], "body": "b"},
        headers={"X-Workflow-Token": "forged"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid or expired workflow token"


# ── item_routes.py: msgspec.ValidationError → 422 ────────────────────


def test_create_app_item_bad_enum_is_422():
    """A body whose typed field fails ``msgspec.convert`` (``severity`` is the
    ``Severity`` enum) raises ``msgspec.ValidationError``, mapped to 422."""
    app, _spec = _app()
    client = TestClient(app)
    r = client.post("/a/rca/items", json={"title": "Oven drift", "severity": "NOPE"})
    assert r.status_code == 422
