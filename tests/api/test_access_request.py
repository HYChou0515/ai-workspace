"""Permission-disclosure: POST /kb/collections/:id/request-access — the caller who
can SEE a collection (read_meta) but not read it asks its owner for access. Sends
ONE deduped `access_request` notification to the owner; a caller who can already
read has nothing to request; a caller who can't even see it gets a 404.
"""

import datetime as dt

import msgspec
from specstar import QB, SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.perm import Permission
from workspace_app.resources import Notification, make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client_and_spec(holder: dict[str, str]) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _set_permission(spec: SpecStar, cid: str, permission: Permission, *, by: str) -> None:
    rm = spec.get_resource_manager(Collection)
    coll = rm.get(cid).data
    assert isinstance(coll, Collection)
    with rm.using(by, dt.datetime.now(dt.UTC)):
        rm.update(cid, msgspec.structs.replace(coll, permission=permission))


def _access_requests(spec: SpecStar, recipient: str) -> list[Notification]:
    rm = spec.get_resource_manager(Notification)
    out = []
    for r in rm.list_resources((QB["recipient"] == recipient).build()):
        n = r.data
        assert isinstance(n, Notification)
        if n.kind == "access_request":
            out.append(n)
    return out


def _discoverable_collection(holder: dict[str, str]) -> tuple[TestClient, SpecStar, str]:
    """bob owns a restricted collection that alice may see (read_meta) but not read."""
    holder["id"] = "bob"
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "Sales-2026"}).json()["resource_id"]
    _set_permission(
        spec,
        cid,
        Permission(visibility="restricted", read_meta=["user:alice"]),
        by="bob",
    )
    return client, spec, cid


def test_request_access_notifies_the_owner_once():
    holder: dict[str, str] = {}
    client, spec, cid = _discoverable_collection(holder)

    holder["id"] = "alice"
    body = client.post(f"/kb/collections/{cid}/request-access").json()
    assert body == {"collection_id": cid, "requested": True, "already_readable": False}

    notes = _access_requests(spec, "bob")
    assert len(notes) == 1
    assert notes[0].actor == "alice"
    assert "Sales-2026" in notes[0].title
    assert notes[0].link == f"/kb/collections/{cid}"


def test_a_repeat_request_does_not_re_notify():
    holder: dict[str, str] = {}
    client, spec, cid = _discoverable_collection(holder)

    holder["id"] = "alice"
    client.post(f"/kb/collections/{cid}/request-access")
    second = client.post(f"/kb/collections/{cid}/request-access").json()

    assert second["requested"] is False  # deduped
    assert len(_access_requests(spec, "bob")) == 1  # still only one notification


def test_a_reader_has_nothing_to_request():
    holder: dict[str, str] = {}
    holder["id"] = "bob"
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "open"}).json()["resource_id"]
    _set_permission(
        spec, cid, Permission(visibility="restricted", read_content=["user:alice"]), by="bob"
    )

    holder["id"] = "alice"
    body = client.post(f"/kb/collections/{cid}/request-access").json()
    assert body["requested"] is False
    assert body["already_readable"] is True
    assert _access_requests(spec, "bob") == []


def test_a_user_who_cannot_see_the_collection_gets_a_404():
    holder: dict[str, str] = {}
    holder["id"] = "bob"
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    _set_permission(spec, cid, Permission(visibility="private"), by="bob")

    holder["id"] = "carol"  # no read_meta → must not even learn it exists
    resp = client.post(f"/kb/collections/{cid}/request-access")
    assert resp.status_code == 404
    assert _access_requests(spec, "bob") == []
