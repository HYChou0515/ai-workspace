"""#303 — SourceDoc read access inherits its collection's visibility.

A doc that lives in a collection the caller can't see is a uniform 404 at the
storage layer (so even the auto-CRUD ``GET /source-doc/{id}`` is hidden, not just
the hand-written routes). This is driven by a DENORMALIZED mirror of the parent
collection's ``visibility`` / ``read_meta`` / ``created_by`` on the SourceDoc plus
a ``source_doc`` access_scope — the doc analogue of ``collection_access_scope``.
"""

import datetime as dt

import msgspec
import pytest
from specstar import SpecStar
from specstar.types import Binary, ResourceIDNotFoundError

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection, SourceDoc
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _ingestor(spec) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    return Ingestor(spec, pipeline=build_doc_pipeline(embedder=embedder), embedder=embedder)


def _client_and_spec(
    holder: dict[str, str], *, superusers: frozenset[str] = frozenset()
) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
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


def _set_permission(spec: SpecStar, cid: str, permission: Permission, *, by: str = "bob") -> None:
    rm = spec.get_resource_manager(Collection)
    coll = rm.get(cid).data
    assert isinstance(coll, Collection)
    with rm.using(by, dt.datetime.now(dt.UTC)):
        rm.update(cid, msgspec.structs.replace(coll, permission=permission))


def test_doc_in_a_private_collection_is_hidden_from_a_non_owner():
    """Storage-layer 404 (the auto-CRUD path) for a non-owner; the owner reads it."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="secret", permission=Permission(visibility="private"))
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "notes.md")
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="notes.md",
                content=Binary(data=b"secret notes"),
                collection_visibility="private",
                collection_read_meta=[],
                collection_created_by="bob",
            ),
            resource_id=doc_id,
        )
    # the collection owner reads their own doc
    with drm.using("bob", apply_access_scope=True):  # ty: ignore[unknown-argument]
        got = drm.get(doc_id).data
        assert isinstance(got, SourceDoc)
        assert got.path == "notes.md"
    # an ordinary non-owner is hidden — a uniform 404 at the storage layer
    with (
        drm.using("alice", apply_access_scope=True),  # ty: ignore[unknown-argument]
        pytest.raises(ResourceIDNotFoundError),
    ):
        drm.get(doc_id)


def test_superuser_reads_a_doc_in_a_private_collection():
    """A configured superuser sees a doc a non-member is 404'd from — the source_doc
    access_scope honours the superuser bypass (acceptance: superuser reads normally)."""
    spec = make_spec(default_user=lambda: "bob", superusers=frozenset({"root"}))
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="secret", permission=Permission(visibility="private"))
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "notes.md")
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="notes.md",
                content=Binary(data=b"x"),
                collection_visibility="private",
                collection_created_by="bob",
            ),
            resource_id=doc_id,
        )
    with drm.using("root", apply_access_scope=True):  # ty: ignore[unknown-argument]
        got = drm.get(doc_id).data
        assert isinstance(got, SourceDoc)
        assert got.path == "notes.md"
    with (
        drm.using("alice", apply_access_scope=True),  # ty: ignore[unknown-argument]
        pytest.raises(ResourceIDNotFoundError),
    ):
        drm.get(doc_id)


def test_doc_create_mirrors_the_collection_permission():
    """A freshly-stored doc denormalizes its collection's visibility / read_meta /
    owner, so its access_scope hides it without any manual mirror set."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(
                name="c",
                permission=Permission(visibility="restricted", read_meta=["user:alice"]),
            )
        ).resource_id
    ids = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello world")
    doc = spec.get_resource_manager(SourceDoc).get(ids[0]).data
    assert isinstance(doc, SourceDoc)
    assert doc.collection_visibility == "restricted"
    assert doc.collection_read_meta == ["user:alice"]
    assert doc.collection_created_by == "bob"


def test_render_document_gates_on_live_collection_read_content():
    """`GET /kb/documents?id=` reads doc CONTENT — guarded against the collection's
    LIVE `read_content` (not the doc's stale mirror): a non-member is 404, an
    in-scope member lacking `read_content` is 403, the owner reads it. This holds
    even before any fan-out re-mirrors the doc, because the route reads the live
    collection permission."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")[0]
    # tighten AFTER upload: alice may see it exists (read_meta) but not read content.
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:alice"]))
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 200  # bob (owner)
    holder["id"] = "alice"
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 403  # in scope, no read_content
    holder["id"] = "carol"
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 404  # not even read_meta


def test_list_doc_chunks_gates_on_read_content():
    """The chunks debug view exposes doc text — same read_content route-guard."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")[0]
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:alice"]))
    assert client.get(f"/kb/documents/chunks?id={doc_id}").status_code == 200  # bob
    holder["id"] = "alice"
    assert client.get(f"/kb/documents/chunks?id={doc_id}").status_code == 403  # no read_content
    holder["id"] = "carol"
    assert client.get(f"/kb/documents/chunks?id={doc_id}").status_code == 404  # no read_meta


def test_collection_export_gates_on_read_content():
    """`POST …/download/prepare` (full export) reads every doc's bytes — gated on
    the collection's read_content."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:alice"]))
    prep = f"/kb/collections/{cid}/download/prepare"
    assert client.post(prep).status_code == 200  # bob (owner)
    holder["id"] = "alice"
    assert client.post(prep).status_code == 403  # in scope, no read_content
    holder["id"] = "carol"
    assert client.post(prep).status_code == 404  # no read_meta


def test_tightening_a_collection_fans_out_to_hide_its_docs_on_the_auto_crud_route():
    """The whole point of the denormalized mirror: after the owner tightens the
    collection via the setter, a fan-out re-mirrors every doc so even the
    auto-CRUD `GET /source-doc/{id}` (which the route-guards can't cover) hides
    the doc from a non-member."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")[0]
    # while the collection is public, alice reads the doc via the auto-CRUD route
    holder["id"] = "alice"
    assert client.get(f"/source-doc/{doc_id}").status_code == 200
    # bob tightens the collection to private through the setter
    holder["id"] = "bob"
    assert (
        client.put(f"/kb/collections/{cid}/permission", json={"visibility": "private"}).status_code
        == 200
    )
    # the fan-out propagated the mirror → alice is now hidden at the storage layer
    holder["id"] = "alice"
    assert client.get(f"/source-doc/{doc_id}").status_code == 404
    # bob (owner) still reads it
    holder["id"] = "bob"
    assert client.get(f"/source-doc/{doc_id}").status_code == 200


def test_push_mirror_to_docs_updates_changed_then_skips_unchanged():
    """The fan-out primitive updates every doc whose mirror differs (returning the
    count) and skips docs already at the target on a repeat call."""
    from workspace_app.kb.doc_permission import push_mirror_to_docs

    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    ing = _ingestor(spec)
    ing.store(collection_id=cid, user="bob", filename="a.md", data=b"one")
    ing.store(collection_id=cid, user="bob", filename="b.md", data=b"two")
    # both docs start public → tightening changes both
    assert push_mirror_to_docs(spec, cid, visibility="private", read_meta=[], created_by="bob") == 2
    # a repeat with the same target skips both (no revision churn)
    assert push_mirror_to_docs(spec, cid, visibility="private", read_meta=[], created_by="bob") == 0
    doc = spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "a.md")).data
    assert isinstance(doc, SourceDoc)
    assert doc.collection_visibility == "private"


def test_chunks_route_returns_empty_for_an_unknown_doc():
    """A missing/deleted doc has no chunks and nothing to authorize — the guard
    keeps the pre-#303 empty-list contract (no existence leak) rather than 404."""
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    r = client.get("/kb/documents/chunks?id=does-not-exist")
    assert r.status_code == 200
    assert r.json() == []


def _spec_with_two_docs() -> tuple[SpecStar, str]:
    """A collection holding two ingested docs — the fan-out's subject."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    ing = _ingestor(spec)
    ing.store(collection_id=cid, user="bob", filename="a.md", data=b"one")
    ing.store(collection_id=cid, user="bob", filename="b.md", data=b"two")
    return spec, cid


def test_push_mirror_to_docs_raises_when_a_doc_could_not_be_mirrored():
    """#434: ``patch_many`` COLLECTS a row it could not write instead of raising,
    so a doc left on the OLD (looser) mirror would otherwise be reported as a
    successful tightening — a silent read leak. The fan-out must fail loudly."""
    from specstar.types import PatchManyResult

    from workspace_app.kb.doc_permission import push_mirror_to_docs

    spec, cid = _spec_with_two_docs()
    drm = spec.get_resource_manager(SourceDoc)
    drm.patch_many = lambda *a, **k: PatchManyResult(  # ty: ignore[invalid-assignment]
        patched=1, failures=[("doc-b", "denied")]
    )
    with pytest.raises(RuntimeError, match="doc-b"):
        push_mirror_to_docs(spec, cid, visibility="private", read_meta=[], created_by="bob")


def test_push_mirror_to_docs_retries_a_conflicted_doc():
    """A doc whose revision moved between selection and write lands in
    ``conflicts`` — expected while indexing writes to the same rows. The patch is
    idempotent and a no-op costs no revision, so the fan-out RE-RUNS rather than
    leaving that doc stale-visible; only a conflict that survives the retry raises."""
    from specstar.types import PatchManyResult

    from workspace_app.kb.doc_permission import push_mirror_to_docs

    spec, cid = _spec_with_two_docs()
    drm = spec.get_resource_manager(SourceDoc)
    calls: list[int] = []

    def flaky(*a, **k) -> PatchManyResult:
        calls.append(1)
        if len(calls) == 1:
            return PatchManyResult(patched=1, conflicts=["doc-b"])
        return PatchManyResult(patched=1)

    drm.patch_many = flaky  # ty: ignore[invalid-assignment]
    assert push_mirror_to_docs(spec, cid, visibility="private", read_meta=[], created_by="bob") == 2
    assert len(calls) == 2
