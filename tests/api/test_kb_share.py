"""P3 — KB chat sharing: private by default, read-only share, shared-with-me."""

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
    """App where both created_by (owner) and the current user follow holder['id']."""
    spec = SpecStar()
    spec.configure(default_user=lambda: holder["id"], default_now=lambda: datetime.now(UTC))
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


def _has(c: TestClient, cid: str) -> bool:
    return any(x["resource_id"] == cid for x in c.get("/kb/chats").json())


def test_share_is_read_only_and_shows_under_shared_with_me():
    holder = {"id": "alice"}
    c = _client(holder)
    cid = c.post("/kb/chats", json={"title": "Reflow", "collection_ids": []}).json()["resource_id"]

    assert c.post(f"/kb/chats/{cid}/share", json={"user_ids": ["bob"]}).status_code == 204

    # bob: got a share notification, can read, sees it in the list, cannot send
    holder["id"] = "bob"
    notifs = c.get("/notifications").json()
    assert any(
        n["kind"] == "share" and n["link"] == f"/kb/chats/{cid}" and n["actor"] == "alice"
        for n in notifs
    )
    got = c.get(f"/kb/chats/{cid}").json()
    assert got["owner"] == "alice" and "bob" in got["shared_with"]
    assert _has(c, cid)
    assert c.post(f"/kb/chats/{cid}/messages", json={"content": "hi"}).status_code == 403

    # carol: no access at all
    holder["id"] = "carol"
    assert c.get(f"/kb/chats/{cid}").status_code == 403
    assert not _has(c, cid)
    # and can't share or delete someone else's chat
    assert c.post(f"/kb/chats/{cid}/share", json={"user_ids": ["dan"]}).status_code == 403
    assert c.delete(f"/kb/chats/{cid}").status_code == 403


def test_share_dedupes_and_skips_self():
    holder = {"id": "alice"}
    c = _client(holder)
    cid = c.post("/kb/chats", json={}).json()["resource_id"]
    c.post(f"/kb/chats/{cid}/share", json={"user_ids": ["alice", "bob"]})  # self skipped
    c.post(f"/kb/chats/{cid}/share", json={"user_ids": ["bob"]})  # already shared → no-op
    assert c.get(f"/kb/chats/{cid}").json()["shared_with"] == ["bob"]


def test_unshare():
    holder = {"id": "alice"}
    c = _client(holder)
    cid = c.post("/kb/chats", json={}).json()["resource_id"]
    c.post(f"/kb/chats/{cid}/share", json={"user_ids": ["bob"]})

    assert c.delete(f"/kb/chats/{cid}/share/ghost").status_code == 204  # not shared → no-op
    assert c.delete(f"/kb/chats/{cid}/share/bob").status_code == 204

    holder["id"] = "bob"
    assert c.get(f"/kb/chats/{cid}").status_code == 403
    assert not _has(c, cid)
