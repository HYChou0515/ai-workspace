"""The current-user seam is single-sourced: create_app makes specstar stamp
created_by with the SAME get_user_id used for access checks, so a request's
owner can never diverge from who the access layer thinks they are.

Regression: real deploys inject a dynamic current user (e.g. read from a
cookie) via get_user_id, while factories.get_spec configures specstar with a
*static* default_user. Without unification a user is stamped owner=default-user
on everything they create, then 403s when fetching their own resource.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox


def _client(holder: dict[str, str]) -> TestClient:
    """Production-shaped wiring: the passed-in spec is configured with a STATIC
    default_user (as get_spec does), and get_user_id is dynamic (the cookie)."""
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
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
    assert c.get(f"/kb/chats/{cid}").status_code == 403
    assert not any(x["resource_id"] == cid for x in c.get("/kb/chats").json())
