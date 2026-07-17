"""PUT /kb/collections/:id/global — the superuser-only toggle that marks a
collection as part of the AI's baseline retrieval scope (global-collection concept,
grill D3: only a superuser may set it)."""

from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _client_and_spec(holder: dict[str, str], *, superusers=frozenset()):
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
        superusers=superusers,
    )
    return TestClient(app), spec


def _is_global(spec: SpecStar, cid: str) -> bool:
    data = spec.get_resource_manager(Collection).get(cid).data
    assert isinstance(data, Collection)
    return data.is_global


def test_superuser_can_toggle_global():
    holder = {"id": "root"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))
    cid = client.post("/kb/collections", json={"name": "Sales-KB"}).json()["resource_id"]
    assert _is_global(spec, cid) is False  # default

    body = client.put(f"/kb/collections/{cid}/global", json={"is_global": True}).json()
    assert body == {"resource_id": cid, "is_global": True}
    assert _is_global(spec, cid) is True

    client.put(f"/kb/collections/{cid}/global", json={"is_global": False})
    assert _is_global(spec, cid) is False  # togglable back off


def test_a_non_superuser_gets_403():
    holder = {"id": "root"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]

    holder["id"] = "alice"  # a plain user — even the owner may not set global
    resp = client.put(f"/kb/collections/{cid}/global", json={"is_global": True})
    assert resp.status_code == 403
    assert _is_global(spec, cid) is False


def test_unknown_collection_is_404():
    holder = {"id": "root"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}))
    resp = client.put("/kb/collections/ghost/global", json={"is_global": True})
    assert resp.status_code == 404


def test_me_reports_superuser_status_for_the_fe():
    # The FE gates the global toggle on /me's is_superuser (no hardcoded set).
    holder = {"id": "root"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}))
    assert client.get("/me").json()["is_superuser"] is True
    holder["id"] = "alice"
    assert client.get("/me").json()["is_superuser"] is False
