"""The current-user seam is single-sourced: the SAME get_user_id
callable threads into both `make_spec(default_user=...)` (so specstar
stamps created_by per request) and `create_app(get_user_id=...)` (so
access checks read the same identity). A request's owner can never
diverge from who the access layer thinks they are.

Regression: real deploys inject a dynamic current user (e.g. read from
a cookie). If `make_spec` is called with a static default_user but
`create_app` gets a dynamic callable, the user is stamped
owner=default-user on everything they create, then 403s when fetching
their own resource. The fix is the same callable in both places —
production wiring does this via `factories.get_spec(settings,
get_user_id=...)` which forwards into `make_spec`.
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client(holder: dict[str, str]) -> TestClient:
    """Production-shaped wiring: ONE `get_user_id` callable goes into
    both make_spec (created_by stamping) and create_app (access checks)."""
    get_user_id = lambda: holder["id"]  # noqa: E731
    spec = make_spec(default_user=get_user_id)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=get_user_id,
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app)


def test_user_owns_and_can_read_back_what_they_create():
    holder = {"id": "alice"}
    c = _client(holder)
    cid = c.post("/kb/chats", json={"title": "t", "collection_ids": []}).json()["resource_id"]

    got = c.get(f"/kb/chats/{cid}")
    assert got.status_code == 200
    assert got.json()["owner"] == "alice"  # created_by followed the cookie
    assert any(x["resource_id"] == cid for x in c.get("/kb/chats").json())


def test_a_different_user_is_still_excluded():
    holder = {"id": "alice"}
    c = _client(holder)
    cid = c.post("/kb/chats", json={"title": "t", "collection_ids": []}).json()["resource_id"]

    holder["id"] = "bob"
    # #304: a stranger gets 404 (not owner-only 403) — the access scope hides
    # a chat they can't read rather than confirming it exists.
    assert c.get(f"/kb/chats/{cid}").status_code == 404
    assert not any(x["resource_id"] == cid for x in c.get("/kb/chats").json())
