"""#534 slice 2 — the permission endpoints carry a change down to the claims.

A claim's mirror is a COPY of the deck's read permission, taken at extraction
time. Copies go stale, so every place a permission is edited has to push the new
verdict down. These are the WIRING tests: the fan-out primitives are unit-tested
in ``tests/kb/test_graph_claim_perm.py``, and what can still go wrong is an
endpoint that changes a permission and forgets to call one — which fails silently,
in the leaking direction, and only for collections that happen to have claims.
"""

from __future__ import annotations

from specstar import SpecStar
from specstar.types import Binary, ResourceIDNotFoundError

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim
from workspace_app.resources.kb import EMBED_DIM, Collection, SourceDoc
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


def _seed(spec: SpecStar) -> tuple[str, str, str]:
    """A public collection holding one deck, with one metric extracted from it."""
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="reports")).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = f"{cid}∕deck.pptx"
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            ),
            resource_id=doc_id,
        )
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        claim_id = grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id=doc_id,
                norm_subject="acme",
                subject="Acme",
                norm_attribute="revenue",
                attribute="Revenue",
                value="1.2M",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        ).resource_id
    return cid, doc_id, claim_id


def _readable(spec: SpecStar, user: str, claim_id: str) -> bool:
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using(user, apply_access_scope=True):  # ty: ignore[unknown-argument]
        try:
            rm.get(claim_id)
        except ResourceIDNotFoundError:
            return False
        return True


def test_tightening_a_collection_hides_the_metrics_extracted_from_it():
    """Locking a collection has to lock its numbers too — otherwise the deck is
    unreachable while "Revenue 1.2M, from deck.pptx" stays readable."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _cid, _doc, claim_id = _seed(spec)
    assert _readable(spec, "alice", claim_id) is True

    r = client.put(
        f"/kb/collections/{_cid}/permission",
        json={
            "visibility": "restricted",
            "read_meta": ["user:amy"],
            "read_content": ["user:amy"],
        },
    )
    assert r.status_code == 200
    assert _readable(spec, "alice", claim_id) is False
    assert _readable(spec, "amy", claim_id) is True


def test_tightening_one_deck_hides_only_that_decks_metrics():
    """A per-deck override reaches the claims of that deck and no others."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid, doc_id, claim_id = _seed(spec)
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        neighbour = grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id=f"{cid}∕other.pptx",
                norm_subject="acme",
                subject="Acme",
                norm_attribute="revenue",
                attribute="Revenue",
                value="9M",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        ).resource_id

    r = client.put(
        f"/kb/documents/{doc_id}/permission",
        json={
            "visibility": "restricted",
            "read_meta": ["user:amy"],
            "read_content": ["user:amy"],
        },
    )
    assert r.status_code == 200
    assert _readable(spec, "alice", claim_id) is False
    assert _readable(spec, "amy", claim_id) is True
    assert _readable(spec, "alice", neighbour) is True


def test_clearing_a_deck_override_reopens_its_metrics():
    """Reverting a deck to pure inheritance has to travel down as well, or the
    claims stay locked after the deck itself is readable again."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _cid, doc_id, claim_id = _seed(spec)
    client.put(
        f"/kb/documents/{doc_id}/permission",
        json={"visibility": "restricted", "read_content": []},
    )
    assert _readable(spec, "alice", claim_id) is False

    r = client.delete(f"/kb/documents/{doc_id}/permission")
    assert r.status_code == 200
    assert _readable(spec, "alice", claim_id) is True


def test_deleting_a_deck_takes_its_metrics_with_it():
    """Deleting a deck must not leave its numbers behind. Claims are keyed on the
    deck (not content-addressed like chunks, so no refcount question), and an
    orphaned claim keeps whatever mirror it last held — readable, and no longer
    reachable by any fan-out keyed on a live doc."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _cid, doc_id, claim_id = _seed(spec)
    assert _readable(spec, "bob", claim_id) is True

    r = client.delete("/kb/documents", params={"id": doc_id})
    assert r.status_code == 200
    grm = spec.get_resource_manager(GraphClaim)
    try:
        grm.get(claim_id)
    except ResourceIDNotFoundError:
        return
    raise AssertionError("the deleted deck's claim is still there")


def test_renaming_a_deck_does_not_leave_its_old_metrics_behind():
    """A rename re-creates the doc under a NEW id, so the old id's claims would
    dangle forever: never wiped by a re-extraction (which only touches the id it is
    processing) and counted a second time the next time the deck is read."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    _cid, doc_id, claim_id = _seed(spec)

    r = client.post("/kb/documents/move", params={"id": doc_id, "to": "renamed.pptx"})
    assert r.status_code == 200, r.text
    grm = spec.get_resource_manager(GraphClaim)
    try:
        grm.get(claim_id)
    except ResourceIDNotFoundError:
        return
    raise AssertionError("the renamed deck's old claims are still there")
