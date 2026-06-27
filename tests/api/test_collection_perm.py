"""#262 PR2 — collection access control: the list is filtered to what the
caller may see, single-resource access is gated, and owners set permissions.

Tests drive the HTTP surface as different users via a mutable `holder["id"]`.
"""

import msgspec
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
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


def _set_permission(spec: SpecStar, cid: str, permission: Permission) -> None:
    rm = spec.get_resource_manager(Collection)
    coll = rm.get(cid).data
    assert isinstance(coll, Collection)
    rm.update(cid, msgspec.structs.replace(coll, permission=permission))


def _names(client: TestClient) -> set[str]:
    return {c["name"] for c in client.get("/kb/collections").json()}


def test_list_collections_hides_a_private_collection_from_a_non_owner():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    # bob owns a collection and locks it down to private.
    secret = client.post("/kb/collections", json={"name": "secret"}).json()["resource_id"]
    _set_permission(spec, secret, Permission(visibility="private"))
    # alice owns a public (default) one.
    holder["id"] = "alice"
    client.post("/kb/collections", json={"name": "open"})
    # alice's list shows the public one but not bob's private one.
    names = _names(client)
    assert "open" in names
    assert "secret" not in names
