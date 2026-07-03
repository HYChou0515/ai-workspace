"""#304 — KbChat access control: a chat carries a first-class `Permission` and is
PRIVATE by default (unlike a collection, whose absent ≡ public). Reads/lists are
gated by `kbchat_access_scope` (404), the per-verb chat actions by `authorize`
at the hand-written routes (share/setter = change_permission, send = converse,
rename = write_meta, delete = owner). A pre-#304 row's legacy `shared_with` still
grants read access via the scope fallback until the one-off migration folds it
into `permission`.

Tests drive the HTTP surface as different users through a mutable `holder["id"]`.
"""

from collections.abc import AsyncIterator

from specstar import SpecStar

from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chat_permission import (
    effective_permission,
    permission_from_shared_with,
)
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, KbChat
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _ReplyRunner:
    """Minimal KB agent: answers deterministically so a send-message turn persists
    both the user message and an assistant reply (exercising the owner-acting
    writes the converse gate relies on)."""

    async def run(self, prompt: str, ctx: object) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="ok")
        yield RunDone()


def _client_and_spec(
    holder: dict[str, str], *, superusers: frozenset[str] = frozenset()
) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_ReplyRunner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
        superusers=superusers,
    )
    return TestClient(app), spec


def _new_chat(client: TestClient, *, title: str = "t") -> str:
    return client.post("/kb/chats", json={"title": title, "collection_ids": []}).json()[
        "resource_id"
    ]


def _new_legacy_chat(spec: SpecStar, *, by: str, shared_with: list[str]) -> str:
    """A pre-#304 row: no `permission`, only the legacy `shared_with` list (created
    directly via the manager so `created_by` = the owner)."""
    rm = spec.get_resource_manager(KbChat)
    with rm.using(by):
        return rm.create(KbChat(title="legacy", shared_with=shared_with)).resource_id


# ── absent-permission default is PRIVATE ────────────────────────────────────


def test_a_new_chat_is_private_hidden_from_others():
    """A freshly-created chat is owner-only: a non-owner 404s the single GET and
    doesn't see it in their list; the owner reads it."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    assert client.get(f"/kb/chats/{cid}").status_code == 200  # owner reads it
    assert cid in {c["resource_id"] for c in client.get("/kb/chats").json()}
    holder["id"] = "alice"
    assert client.get(f"/kb/chats/{cid}").status_code == 404  # hidden from a stranger
    assert cid not in {c["resource_id"] for c in client.get("/kb/chats").json()}


# ── read-only share (change_permission to grant, read_chat to view) ─────────


def test_share_lets_a_user_read_but_not_send():
    """Sharing grants read_meta + read_chat (a viewer, not a sender): the shared
    user reads the thread (200) and appears in `shared_with`, but sending is 403
    (no converse)."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    assert client.post(f"/kb/chats/{cid}/share", json={"user_ids": ["alice"]}).status_code == 204
    body = client.get(f"/kb/chats/{cid}").json()
    assert body["shared_with"] == ["alice"]
    holder["id"] = "alice"
    assert client.get(f"/kb/chats/{cid}").status_code == 200  # can read
    assert (
        client.post(f"/kb/chats/{cid}/messages", json={"content": "hi"}).status_code == 403
    )  # cannot send


def test_only_the_owner_can_share():
    """Sharing needs change_permission — the owner has it, an unrelated user 404s
    (can't even see the chat), an in-scope viewer 403s (read but not rewire)."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    client.post(f"/kb/chats/{cid}/share", json={"user_ids": ["alice"]})
    holder["id"] = "carol"  # a stranger can't see it
    assert client.post(f"/kb/chats/{cid}/share", json={"user_ids": ["x"]}).status_code == 404
    holder["id"] = "alice"  # a viewer can read but not share
    assert client.post(f"/kb/chats/{cid}/share", json={"user_ids": ["x"]}).status_code == 403


def test_unshare_revokes_access():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    client.post(f"/kb/chats/{cid}/share", json={"user_ids": ["alice"]})
    assert client.delete(f"/kb/chats/{cid}/share/alice").status_code == 204
    holder["id"] = "alice"
    assert client.get(f"/kb/chats/{cid}").status_code == 404  # revoked → hidden


# ── converse grant → a collaborator can send ────────────────────────────────


def test_setter_grants_converse_and_a_collaborator_can_send():
    """The permission setter can grant `converse`: a collaborator with read_meta +
    read_chat + converse both reads AND sends (the send persists as the owner, so
    the auto-CRUD write handler doesn't block a converse-only collaborator)."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    r = client.put(
        f"/kb/chats/{cid}/permission",
        json={
            "visibility": "restricted",
            "read_meta": ["user:alice"],
            "read_chat": ["user:alice"],
            "converse": ["user:alice"],
        },
    )
    assert r.status_code == 200
    assert r.json()["notified"] == ["alice"]
    holder["id"] = "alice"
    assert client.post(f"/kb/chats/{cid}/messages", json={"content": "hi"}).status_code == 200
    # the collaborator's message persisted, attributed to them
    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    assert any(m["role"] == "user" and m["author"] == "alice" for m in msgs)


def test_only_change_permission_holder_can_use_the_setter():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    client.put(
        f"/kb/chats/{cid}/permission",
        json={"visibility": "restricted", "read_meta": ["user:alice"], "read_chat": ["user:alice"]},
    )
    holder["id"] = "alice"  # in scope (read) but no change_permission
    assert (
        client.put(f"/kb/chats/{cid}/permission", json={"visibility": "public"}).status_code == 403
    )


def test_setter_rejects_an_invalid_visibility():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    assert (
        client.put(f"/kb/chats/{cid}/permission", json={"visibility": "bogus"}).status_code == 400
    )


# ── rename (write_meta) + delete (owner-only) ───────────────────────────────


def test_rename_is_gated_on_write_meta():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    client.put(
        f"/kb/chats/{cid}/permission",
        json={"visibility": "restricted", "read_meta": ["user:alice"], "read_chat": ["user:alice"]},
    )
    holder["id"] = "alice"  # viewer, no write_meta
    assert client.patch(f"/kb/chats/{cid}", json={"title": "hijack"}).status_code == 403
    holder["id"] = "bob"  # owner renames
    assert client.patch(f"/kb/chats/{cid}", json={"title": "renamed"}).status_code == 200
    assert client.get(f"/kb/chats/{cid}").json()["title"] == "renamed"


def test_delete_is_owner_only():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    cid = _new_chat(client)
    client.post(f"/kb/chats/{cid}/share", json={"user_ids": ["alice"]})
    holder["id"] = "carol"  # stranger → 404 (no existence leak)
    assert client.delete(f"/kb/chats/{cid}").status_code == 404
    holder["id"] = "alice"  # viewer → 403 (can see but not destroy)
    assert client.delete(f"/kb/chats/{cid}").status_code == 403
    holder["id"] = "bob"  # owner → 204
    assert client.delete(f"/kb/chats/{cid}").status_code == 204


# ── superuser bypass ────────────────────────────────────────────────────────


def test_a_superuser_sees_a_private_chat():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}))
    cid = _new_chat(client)
    holder["id"] = "root"
    assert client.get(f"/kb/chats/{cid}").status_code == 200
    assert cid in {c["resource_id"] for c in client.get("/kb/chats").json()}


# ── legacy shared_with fallback (pre-#304 rows, no permission yet) ───────────


def test_legacy_shared_with_still_grants_read_before_migration():
    """A pre-#304 chat has no `permission`, only `shared_with`. The access_scope's
    fallback clause keeps it readable by a shared user (and hidden from others)
    until the one-off migration runs."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = _new_legacy_chat(spec, by="bob", shared_with=["alice"])
    holder["id"] = "alice"
    assert client.get(f"/kb/chats/{cid}").status_code == 200  # fallback grants read
    holder["id"] = "carol"
    assert client.get(f"/kb/chats/{cid}").status_code == 404  # not shared → hidden


def test_sharing_a_legacy_chat_migrates_it_through_the_write_acl():
    """Writing to a pre-#304 chat (stored permission is None) must authorize
    against its EFFECTIVE permission, not the world-writable `public` a bare None
    implies. The owner shares a legacy chat → the write-ACL's absent-permission
    hook synthesises the effective (restricted) permission from `shared_with`, the
    owner passes, and the write folds both the legacy and the new share into
    `permission` (clearing `shared_with`)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = _new_legacy_chat(spec, by="bob", shared_with=["alice"])
    assert client.post(f"/kb/chats/{cid}/share", json={"user_ids": ["carol"]}).status_code == 204
    body = client.get(f"/kb/chats/{cid}").json()
    assert body["shared_with"] == ["alice", "carol"]  # legacy + new share, both readable
    # both the pre-existing and the new share can now read the migrated chat
    for uid in ("alice", "carol"):
        holder["id"] = uid
        assert client.get(f"/kb/chats/{cid}").status_code == 200


# ── migration + effective-permission unit ───────────────────────────────────


def test_permission_from_shared_with_maps_viewers_to_read_grants():
    """A shared user becomes a read_meta + read_chat grant under restricted — NOT
    converse (they were read-only)."""
    perm = permission_from_shared_with(["alice", "bob"])
    assert perm.visibility == "restricted"
    assert perm.read_meta == ["user:alice", "user:bob"]
    assert perm.read_chat == ["user:alice", "user:bob"]
    assert perm.converse == []


def test_permission_from_shared_with_empty_is_private():
    assert permission_from_shared_with([]) == Permission(visibility="private")


def test_effective_permission_prefers_stored_over_legacy():
    stored = Permission(visibility="public")
    assert effective_permission(stored, ["alice"]) is stored
    assert effective_permission(None, ["alice"]).visibility == "restricted"
    assert effective_permission(None, []).visibility == "private"


def test_migration_folds_shared_with_into_permission_and_clears_it():
    """The Schema step (run by `POST /kb-chat/migrate/execute` over pre-v2 rows)
    folds `shared_with` into a `Permission` and clears the legacy field, so a
    migrated row is authorized off `permission` alone."""
    from workspace_app.resources import _migrate_kbchat_shared_with

    out = _migrate_kbchat_shared_with(KbChat(title="legacy", shared_with=["alice", "bob"]))
    assert isinstance(out, KbChat)
    assert out.shared_with == []
    assert out.permission == permission_from_shared_with(["alice", "bob"])


def test_migration_leaves_a_row_that_already_has_permission_untouched():
    from workspace_app.resources import _migrate_kbchat_shared_with

    chat = KbChat(title="x", permission=Permission(visibility="public"), shared_with=["alice"])
    out = _migrate_kbchat_shared_with(chat)
    assert out is chat  # already carries a permission → no-op


def test_app_mounts_the_kbchat_migrate_route():
    """The one-off backfill is the supported `POST /kb-chat/migrate/execute` (the
    global MigrateRouteTemplate), so an operator can fold legacy shares in bulk."""
    from fastapi import FastAPI

    app = FastAPI()
    make_spec().apply(app)
    paths = {p for r in app.routes if (p := getattr(r, "path", None)) is not None}
    assert "/kb-chat/migrate/execute" in paths
