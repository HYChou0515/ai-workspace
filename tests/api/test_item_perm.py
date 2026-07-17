"""#306 PR3 — per-WorkItem enforcement on the hand-written workspace sub-routes.

The item auto-CRUD is storage-gated (read_meta → 404), but the workspace files /
chat / stream sub-routes went through ``locator.require_item`` (slug↔item only) and
enforced nothing. Now every one funnels through ``locator.require_access(verb)``:
files → read_content, chat send → converse, stream/thread → read_chat, with the
read_meta 404 gate first. The chat thread (Conversation auto-CRUD) is gated by the
denormalized item→conversation mirror + ``conversation_access_scope``.
"""

import datetime as dt

import msgspec
from specstar import QB, SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client_and_spec(holder: dict[str, str], *, superusers=frozenset()):
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=superusers,
    )
    return TestClient(app), spec


def _item(spec: SpecStar, *, by: str, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(RcaInvestigation(title="t", owner=by, permission=permission)).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


# ── the read_meta 404 gate (private item, non-owner) ───────────────────────────


def test_private_item_hides_files_and_chat_from_a_non_owner():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))

    holder["id"] = "alice"  # no read_meta → uniform 404 everywhere
    assert client.get(_wp(iid, "/files")).status_code == 404
    assert client.get(_wp(iid, "/files/notes.md")).status_code == 404
    assert client.post(_wp(iid, "/messages"), json={"content": "hi"}).status_code == 404


def test_owner_reaches_everything_on_their_private_item():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))
    assert client.get(_wp(iid, "/files")).status_code == 200


# ── read_meta vs read_content vs converse (restricted grants) ──────────────────


def test_read_meta_only_can_see_but_not_read_content_403():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=["user:alice"])
    )
    holder["id"] = "alice"
    # has read_meta (so NOT 404) but no read_content → 403 on the files
    assert client.get(_wp(iid, "/files")).status_code == 403


def test_read_content_grant_opens_files_but_not_converse():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec,
        by="bob",
        permission=Permission(
            visibility="restricted", read_meta=["user:alice"], read_content=["user:alice"]
        ),
    )
    holder["id"] = "alice"
    assert client.get(_wp(iid, "/files")).status_code == 200
    # can read files, but not drive the agent — converse is a separate verb
    assert client.post(_wp(iid, "/messages"), json={"content": "hi"}).status_code == 403


def test_converse_grant_opens_sending():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec,
        by="bob",
        permission=Permission(
            visibility="restricted", read_meta=["user:alice"], converse=["user:alice"]
        ),
    )
    holder["id"] = "alice"
    r = client.post(_wp(iid, "/messages"), json={"content": "hi"})
    assert r.status_code == 202  # accepted — the turn runs async


# ── Conversation thread read gated by read_chat (denormalized mirror) ──────────


def _conversations_for(client: TestClient, iid: str) -> list:
    # the FE reads the thread via the Conversation auto-CRUD, filtered by item_id
    import json

    cond = json.dumps([{"field_path": "item_id", "operator": "eq", "value": iid}])
    resp = client.get("/conversation", params={"data_conditions": cond})
    return resp.json() if resp.status_code == 200 else []


def test_thread_is_hidden_without_read_chat_and_visible_with_it():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=["user:alice"])
    )
    # owner opens a chat (stamps the mirror = restricted, no read_chat grant to alice)
    conv_rm = spec.get_resource_manager(Conversation)
    with conv_rm.using("bob"):
        from workspace_app.api.item_conversation_perm import item_conversation_mirror

        conv_rm.create(Conversation(item_id=iid, **item_conversation_mirror(spec, iid)))

    holder["id"] = "alice"  # read_meta only → the thread is hidden
    assert _conversations_for(client, iid) == []

    # owner grants alice read_chat → the fan-out re-stamps → thread becomes visible
    holder["id"] = "bob"
    r = client.put(
        _wp(iid, "/permission"),
        json={"visibility": "restricted", "read_meta": ["user:alice"], "read_chat": ["user:alice"]},
    )
    assert r.status_code == 200
    holder["id"] = "alice"
    assert len(_conversations_for(client, iid)) == 1


def test_new_item_created_via_route_defaults_to_private():
    # grill D6: a freshly created item is owner-only until shared.
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = client.post("/a/rca/items", json={"title": "secret"}).json()["resource_id"]

    item = spec.get_resource_manager(RcaInvestigation).get(iid).data
    assert isinstance(item, RcaInvestigation)
    assert item.permission is not None and item.permission.visibility == "private"

    holder["id"] = "alice"  # a non-owner can't even see it
    assert client.get(_wp(iid, "/files")).status_code == 404


def test_superuser_reaches_a_private_item():
    holder = {"id": "root"}
    su = frozenset({"root"})
    client, spec = _client_and_spec(holder, superusers=su)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))
    assert client.get(_wp(iid, "/files")).status_code == 200


def test_adding_a_member_grants_participant_access():
    # grill D7: a member auto-gets read_chat + read_content + converse.
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))
    assert client.put(_wp(iid, "/members"), json={"members": ["alice"]}).status_code == 200

    holder["id"] = "alice"
    assert client.get(_wp(iid, "/files")).status_code == 200  # read_content
    assert client.post(_wp(iid, "/messages"), json={"content": "hi"}).status_code == 202  # converse

    item = spec.get_resource_manager(RcaInvestigation).get(iid).data
    assert isinstance(item, RcaInvestigation)
    assert item.permission is not None
    assert item.permission.visibility == "restricted"
    assert "user:alice" in item.permission.read_chat
    assert "user:alice" in item.permission.converse


def test_removing_a_member_strips_their_grants():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))
    client.put(_wp(iid, "/members"), json={"members": ["alice", "dave"]})
    client.put(_wp(iid, "/members"), json={"members": ["dave"]})  # drop alice

    holder["id"] = "alice"
    assert client.get(_wp(iid, "/files")).status_code == 404  # stripped → can't even see it
    item = spec.get_resource_manager(RcaInvestigation).get(iid).data
    assert isinstance(item, RcaInvestigation)
    assert item.permission is not None
    assert "user:alice" not in item.permission.read_chat
    assert "user:dave" in item.permission.read_chat  # the kept member stays granted


def test_editing_members_requires_change_permission():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=["user:alice"])
    )
    holder["id"] = "alice"  # can see it (read_meta) but not change_permission → 403
    assert client.put(_wp(iid, "/members"), json={"members": ["mallory"]}).status_code == 403


def _access_requests(spec: SpecStar, recipient: str) -> list:
    from workspace_app.resources import Notification

    rm = spec.get_resource_manager(Notification)
    out = []
    for r in rm.list_resources((QB["recipient"] == recipient).build()):
        n = r.data
        assert isinstance(n, Notification)
        if n.kind == "access_request":
            out.append(n)
    return out


def test_request_access_notifies_owner_once_and_dedupes():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=["user:alice"])
    )
    holder["id"] = "alice"
    body = client.post(_wp(iid, "/request-access")).json()
    assert body == {"item_id": iid, "requested": True, "already_readable": False}
    assert len(_access_requests(spec, "bob")) == 1

    second = client.post(_wp(iid, "/request-access")).json()
    assert second["requested"] is False  # deduped
    assert len(_access_requests(spec, "bob")) == 1


def test_request_access_already_readable_for_someone_who_can_enter():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(
        spec,
        by="bob",
        permission=Permission(
            visibility="restricted", read_meta=["user:alice"], read_chat=["user:alice"]
        ),
    )
    holder["id"] = "alice"
    body = client.post(_wp(iid, "/request-access")).json()
    assert body["already_readable"] is True and body["requested"] is False
    assert _access_requests(spec, "bob") == []


def test_request_access_404_when_cannot_even_see_the_item():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))
    holder["id"] = "carol"
    assert client.post(_wp(iid, "/request-access")).status_code == 404
    assert _access_requests(spec, "bob") == []


def test_mirror_stamped_on_default_chat_matches_item(tmp_path=None):
    # sanity: the item→conversation mirror helper reads the live item permission
    holder = {"id": "bob"}
    _client, spec = _client_and_spec(holder)
    iid = _item(
        spec,
        by="bob",
        permission=Permission(visibility="restricted", read_chat=["user:carol"]),
    )
    from workspace_app.api.item_conversation_perm import item_conversation_mirror

    m = item_conversation_mirror(spec, iid)
    assert m == {
        "item_visibility": "restricted",
        "item_read_chat": ["user:carol"],
        "item_created_by": "bob",
    }
    # unused import guard
    assert msgspec is not None and QB is not None and dt is not None
