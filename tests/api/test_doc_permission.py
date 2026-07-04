"""#308 — per-doc permission override.

A SourceDoc may carry its OWN ``Permission`` that TIGHTENS (never loosens) the
read access it inherits from its parent collection (#303). Effective read =
collection-allows AND doc-override-allows. Only the collection owner (+ a
superuser) may set it; the doc uploader gets no special read right.

P1 covers the model + index registration only: the field exists, defaults to "no
override", the collection carries the ``has_doc_overrides`` short-circuit counter,
and the doc's own ``permission.visibility`` is a queryable index.
"""

import msgspec
import pytest
from specstar import QB, SpecStar
from specstar.types import Binary, PermissionDeniedError, ResourceIDNotFoundError

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.groups import Group
from workspace_app.resources.kb import EMBED_DIM, Collection, SourceDoc
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _ingestor(spec: SpecStar) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    return Ingestor(spec, pipeline=build_doc_pipeline(embedder=embedder), embedder=embedder)


def _client_and_spec(
    holder: dict[str, str],
    *,
    superusers: frozenset[str] = frozenset(),
    runner: object | None = None,
) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner if runner is not None else ScriptedAgentRunner([]),  # ty: ignore[invalid-argument-type]
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _set_doc_override(
    spec: SpecStar, doc_id: str, permission: Permission | None, *, by: str = "bob"
) -> None:
    """Set a doc's override directly (the dedicated endpoint is P4)."""
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    with drm.using(by):
        drm.update(doc_id, msgspec.structs.replace(doc, permission=permission))


def _mk_collection(spec: SpecStar, *, by: str = "bob", name: str = "c") -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using(by):
        return crm.create(Collection(name=name)).resource_id


def _mk_doc(
    spec: SpecStar,
    cid: str,
    path: str,
    *,
    by: str = "bob",
    collection_created_by: str = "bob",
    permission: Permission | None = None,
) -> str:
    """A doc in a PUBLIC collection (mirror = public) so the ONLY thing that can
    hide it is its own per-doc override — isolating the #308 intersect."""
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, path)
    with drm.using(by):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path=path,
                content=Binary(data=b"body"),
                collection_visibility="public",
                collection_read_meta=[],
                collection_created_by=collection_created_by,
                permission=permission,
            ),
            resource_id=doc_id,
        )
    return doc_id


def _can_read_at_storage(spec: SpecStar, doc_id: str, user: str) -> bool:
    """Does the storage-layer access_scope admit `user` to this doc? (the 404
    layer covering the auto-CRUD GET /source-doc/{id})."""
    drm = spec.get_resource_manager(SourceDoc)
    try:
        with drm.using(user, apply_access_scope=True):  # ty: ignore[unknown-argument]
            drm.get(doc_id)
        return True
    except ResourceIDNotFoundError:
        return False


def test_source_doc_permission_defaults_to_none() -> None:
    """A doc with no override carries ``permission is None`` — pure inheritance,
    today's behaviour for every doc."""
    doc = SourceDoc(collection_id="c", path="a.md", content=Binary(data=b"x"))
    assert doc.permission is None


def test_collection_has_doc_overrides_defaults_to_zero() -> None:
    """The short-circuit counter starts at 0 — an existing collection decodes to
    "no overrides" with no migration."""
    assert Collection(name="c").has_doc_overrides == 0


def test_doc_permission_visibility_is_a_queryable_index() -> None:
    """The doc's OWN ``permission.visibility`` / ``permission.read_meta`` are
    indexed so the storage-scope (P2) and the AI denylist (P5) can filter on a
    doc's override at the storage layer."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec)
    drm = spec.get_resource_manager(SourceDoc)
    overridden_id = encode_doc_id(cid, "secret.md")
    plain_id = encode_doc_id(cid, "plain.md")
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="secret.md",
                content=Binary(data=b"secret"),
                permission=Permission(visibility="restricted", read_meta=["user:alice"]),
            ),
            resource_id=overridden_id,
        )
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="plain.md",
                content=Binary(data=b"plain"),
            ),
            resource_id=plain_id,
        )
    hits = [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in drm.list_resources((QB["permission.visibility"] == "restricted").build())
    ]
    assert hits == [overridden_id]


# ---------------------------------------------------------------------------
# P2 — storage-scope (404 layer): the doc-override intersect + group grants
# ---------------------------------------------------------------------------


def test_override_hides_a_doc_from_a_collection_reader() -> None:
    """In a PUBLIC collection everyone can read, an override restricting ONE doc to
    alice hides it from carol at the storage layer, while alice (granted), bob (the
    collection owner) still read it — the intersect that TIGHTENS."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec)
    doc_id = _mk_doc(
        spec,
        cid,
        "secret.md",
        permission=Permission(visibility="restricted", read_meta=["user:alice"]),
    )
    assert _can_read_at_storage(spec, doc_id, "alice") is True  # granted
    assert _can_read_at_storage(spec, doc_id, "bob") is True  # collection owner
    assert _can_read_at_storage(spec, doc_id, "carol") is False  # collection reader, blocked


def test_a_plain_doc_in_the_same_collection_is_unaffected_by_an_override() -> None:
    """A doc with NO override stays governed purely by the (public) collection —
    the intersect adds nothing for non-users."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec)
    _mk_doc(spec, cid, "secret.md", permission=Permission(visibility="private"))
    plain = _mk_doc(spec, cid, "plain.md")
    assert _can_read_at_storage(spec, plain, "carol") is True


def test_superuser_bypasses_a_doc_override() -> None:
    spec = make_spec(default_user=lambda: "bob", superusers=frozenset({"root"}))
    cid = _mk_collection(spec)
    doc_id = _mk_doc(spec, cid, "secret.md", permission=Permission(visibility="private"))
    assert _can_read_at_storage(spec, doc_id, "root") is True
    assert _can_read_at_storage(spec, doc_id, "carol") is False


def test_override_read_meta_honours_a_group_grant() -> None:
    """#308/D7: a doc override may grant `group:<id>` — closing the #303 gap where
    the doc storage-scope ignored groups. carol (in eng) reads it; dave doesn't."""
    spec = make_spec(default_user=lambda: "bob")
    grm = spec.get_resource_manager(Group)
    with grm.using("bob"):
        gid = grm.create(Group(name="eng", members=["carol"])).resource_id
    cid = _mk_collection(spec)
    doc_id = _mk_doc(
        spec,
        cid,
        "secret.md",
        permission=Permission(visibility="restricted", read_meta=[f"group:{gid}"]),
    )
    assert _can_read_at_storage(spec, doc_id, "carol") is True  # in group eng
    assert _can_read_at_storage(spec, doc_id, "dave") is False  # not in the group


def test_override_cannot_loosen_a_private_collection() -> None:
    """The intersect only tightens: a doc override granting bob's private
    collection to carol does NOT let carol in — the collection half still blocks."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="secret", permission=Permission(visibility="private"))
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "a.md")
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="a.md",
                content=Binary(data=b"x"),
                collection_visibility="private",  # mirror of the private collection
                collection_created_by="bob",
                permission=Permission(visibility="restricted", read_meta=["user:carol"]),
            ),
            resource_id=doc_id,
        )
    assert _can_read_at_storage(spec, doc_id, "carol") is False  # collection half still hides it


# ---------------------------------------------------------------------------
# P3 — route-guard (403/404) content reads + the document-list filter
# ---------------------------------------------------------------------------


def test_render_document_hides_an_overridden_doc_from_a_collection_reader() -> None:
    """A doc override sequences like the collection guard: an override that blocks
    read_meta is a 404 (hidden), one that grants read_meta but not read_content is
    a 403, and the granted user + the collection owner read it — all inside a
    PUBLIC collection everyone may otherwise read."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")[0]
    _set_doc_override(
        spec,
        doc_id,
        Permission(visibility="restricted", read_meta=["user:alice"], read_content=["user:alice"]),
    )
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 200  # bob (collection owner)
    holder["id"] = "alice"
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 200  # granted
    holder["id"] = "carol"
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 404  # override hides existence


def test_render_document_403_when_override_grants_read_meta_but_not_read_content() -> None:
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")[0]
    # carol may SEE it exists (read_meta) but not read its content.
    _set_doc_override(
        spec, doc_id, Permission(visibility="restricted", read_meta=["user:carol"], read_content=[])
    )
    holder["id"] = "carol"
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 403


def test_chunks_route_honours_the_doc_override() -> None:
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")[0]
    _set_doc_override(
        spec,
        doc_id,
        Permission(visibility="restricted", read_meta=["user:alice"], read_content=["user:alice"]),
    )
    assert client.get(f"/kb/documents/chunks?id={doc_id}").status_code == 200  # bob (owner)
    holder["id"] = "alice"
    assert client.get(f"/kb/documents/chunks?id={doc_id}").status_code == 200  # granted
    holder["id"] = "carol"
    assert client.get(f"/kb/documents/chunks?id={doc_id}").status_code == 404  # override hides it


def test_document_list_filters_out_overridden_docs_per_reader() -> None:
    """The doc list drops docs a caller can't read_meta (via the override), so a
    tightened doc vanishes from a collection reader's list + total, while the owner
    and the granted user still see it."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    ing = _ingestor(spec)
    ing.store(collection_id=cid, user="bob", filename="plain.md", data=b"public one")
    secret_id = ing.store(collection_id=cid, user="bob", filename="secret.md", data=b"top secret")[
        0
    ]
    _set_doc_override(
        spec, secret_id, Permission(visibility="restricted", read_meta=["user:alice"])
    )

    def _list() -> tuple[int, set[str]]:
        page = client.get(f"/kb/collections/{cid}/documents").json()
        return page["total"], {d["path"] for d in page["items"]}

    holder["id"] = "bob"  # owner sees both
    assert _list() == (2, {"plain.md", "secret.md"})
    holder["id"] = "alice"  # granted sees both
    assert _list() == (2, {"plain.md", "secret.md"})
    holder["id"] = "carol"  # collection reader sees only the plain doc
    assert _list() == (1, {"plain.md"})


# ---------------------------------------------------------------------------
# P4 — the write API (set / clear / read) + the auto-CRUD anti-bypass + counter
# ---------------------------------------------------------------------------


def _override_count(spec: SpecStar, cid: str) -> int:
    coll = spec.get_resource_manager(Collection).get(cid).data
    assert isinstance(coll, Collection)
    return coll.has_doc_overrides


def test_owner_sets_reads_and_clears_a_doc_override() -> None:
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hi")[0]
    # set
    r = client.put(
        f"/kb/documents/{doc_id}/permission",
        json={
            "visibility": "restricted",
            "read_meta": ["user:alice"],
            "read_content": ["user:alice"],
        },
    )
    assert r.status_code == 200
    assert r.json()["visibility"] == "restricted"
    # read back
    state = client.get(f"/kb/documents/{doc_id}/permission").json()
    assert state["visibility"] == "restricted"
    assert state["read_meta"] == ["user:alice"]
    # the override is now enforced: carol is hidden
    holder["id"] = "carol"
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 404
    # clear → back to inheritance (public collection ⇒ carol reads again)
    holder["id"] = "bob"
    assert client.delete(f"/kb/documents/{doc_id}/permission").status_code == 200
    holder["id"] = "carol"
    assert client.get(f"/kb/documents?id={doc_id}").status_code == 200


def test_has_doc_overrides_counter_tracks_set_and_clear() -> None:
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    d1 = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"one")[0]
    d2 = _ingestor(spec).store(collection_id=cid, user="bob", filename="b.md", data=b"two")[0]
    assert _override_count(spec, cid) == 0
    client.put(f"/kb/documents/{d1}/permission", json={"visibility": "private"})
    assert _override_count(spec, cid) == 1
    client.put(f"/kb/documents/{d2}/permission", json={"visibility": "private"})
    assert _override_count(spec, cid) == 2
    # re-setting the same doc (still one override) keeps the count exact
    client.put(f"/kb/documents/{d1}/permission", json={"visibility": "restricted"})
    assert _override_count(spec, cid) == 2
    client.delete(f"/kb/documents/{d1}/permission")
    assert _override_count(spec, cid) == 1
    client.delete(f"/kb/documents/{d2}/permission")
    assert _override_count(spec, cid) == 0


def test_a_non_owner_cannot_set_a_doc_override() -> None:
    """In a public collection carol can read the doc, but only the collection owner
    manages its override → 403."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hi")[0]
    holder["id"] = "carol"
    assert (
        client.put(f"/kb/documents/{doc_id}/permission", json={"visibility": "private"}).status_code
        == 403
    )
    assert client.get(f"/kb/documents/{doc_id}/permission").status_code == 403


def test_auto_crud_permission_write_is_blocked_for_a_non_owner() -> None:
    """The anti-bypass: a direct RM update (the auto-CRUD `PUT /source-doc/{id}`
    path) that changes `permission` is denied for a non-owner, but the owner may,
    and a NON-permission write by a non-owner (a re-index status bump) is allowed —
    the checker is narrow."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    doc_id = _mk_doc(spec, cid, "a.md")
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    # a non-owner changing `permission` is denied
    with drm.using("carol"), pytest.raises(PermissionDeniedError):
        drm.update(
            doc_id, msgspec.structs.replace(doc, permission=Permission(visibility="private"))
        )
    # a non-owner write that does NOT touch `permission` is allowed (narrow checker)
    with drm.using("carol"):
        drm.update(doc_id, msgspec.structs.replace(doc, status="ready"))
    # the owner may change `permission`
    with drm.using("bob"):
        drm.update(
            doc_id, msgspec.structs.replace(doc, permission=Permission(visibility="private"))
        )
    assert isinstance(drm.get(doc_id).data, SourceDoc)


# ---------------------------------------------------------------------------
# denied_doc_ids — the shared exclusion helper (backs the list + AI retrieval)
# ---------------------------------------------------------------------------


def test_denied_doc_ids_only_returns_blocked_overridden_docs() -> None:
    from workspace_app.kb.doc_permission import denied_doc_ids
    from workspace_app.perm import Actor

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec)
    plain = _mk_doc(spec, cid, "plain.md")  # no override
    secret = _mk_doc(
        spec,
        cid,
        "secret.md",
        permission=Permission(visibility="restricted", read_meta=["user:alice"]),
    )
    carol = Actor.human("carol")
    # carol is blocked from the overridden doc only; the plain doc is never a candidate
    denied = denied_doc_ids(spec, carol, [cid], "read_meta")
    assert denied == frozenset({secret})
    assert plain not in denied
    # alice is granted → nothing denied
    assert denied_doc_ids(spec, Actor.human("alice"), [cid], "read_meta") == frozenset()
    # the collection owner is never denied (authorize owner branch)
    assert denied_doc_ids(spec, Actor.human("bob"), [cid], "read_meta") == frozenset()


def test_denied_doc_ids_empty_when_no_overrides() -> None:
    from workspace_app.kb.doc_permission import denied_doc_ids
    from workspace_app.perm import Actor

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec)
    _mk_doc(spec, cid, "a.md")
    _mk_doc(spec, cid, "b.md")
    assert denied_doc_ids(spec, Actor.human("carol"), [cid], "read_content") == frozenset()


def test_denied_doc_ids_superuser_never_denied() -> None:
    from workspace_app.kb.doc_permission import denied_doc_ids
    from workspace_app.perm import Actor

    spec = make_spec(default_user=lambda: "bob", superusers=frozenset({"root"}))
    cid = _mk_collection(spec)
    _mk_doc(spec, cid, "secret.md", permission=Permission(visibility="private"))
    assert (
        denied_doc_ids(
            spec, Actor.human("root"), [cid], "read_content", superusers=frozenset({"root"})
        )
        == frozenset()
    )


# ---------------------------------------------------------------------------
# P5 — AI retrieval denylist (end-to-end ctx wiring) + export exclusion
# ---------------------------------------------------------------------------


class _ExcludeRecordingRunner:
    """A KB runner that records the `exclude_doc_ids` the API boundary put on the
    turn context — so we assert the speaker's per-doc-override exclusion reaches
    the retriever seam (the retriever's own filtering is unit-tested separately)."""

    def __init__(self) -> None:
        self.seen: frozenset[str] = frozenset({"__unset__"})

    async def run(self, prompt: str, ctx):  # noqa: ANN001
        from workspace_app.api.events import MessageDelta, RunDone

        self.seen = frozenset(ctx.exclude_doc_ids)
        yield MessageDelta(text="ok")
        yield RunDone()


def test_kb_chat_send_puts_the_speakers_denied_docs_on_the_turn_ctx() -> None:
    holder = {"id": "bob"}
    runner = _ExcludeRecordingRunner()
    client, spec = _client_and_spec(holder, runner=runner)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"secret")[
        0
    ]
    client.put(
        f"/kb/documents/{doc_id}/permission",
        json={
            "visibility": "restricted",
            "read_meta": ["user:alice"],
            "read_content": ["user:alice"],
        },
    )
    # carol (blocked from the doc) chats over the collection → the doc is excluded
    holder["id"] = "carol"
    chat = client.post("/kb/chats", json={"collection_ids": [cid]}).json()["resource_id"]
    client.post(f"/kb/chats/{chat}/messages", json={"content": "what do the docs say?"})
    assert runner.seen == frozenset({doc_id})
    # bob (the collection owner) is never denied
    holder["id"] = "bob"
    chat2 = client.post("/kb/chats", json={"collection_ids": [cid]}).json()["resource_id"]
    client.post(f"/kb/chats/{chat2}/messages", json={"content": "hi"})
    assert runner.seen == frozenset()


def test_collection_export_excludes_denied_docs(tmp_path) -> None:  # noqa: ANN001
    import json
    import zipfile

    from workspace_app.kb.collection_export import build_collection_zip

    spec = make_spec(default_user=lambda: "bob")
    cid = _mk_collection(spec)
    _mk_doc(spec, cid, "public.md")
    secret = _mk_doc(
        spec,
        cid,
        "secret.md",
        permission=Permission(visibility="restricted", read_meta=["user:alice"]),
    )
    out = tmp_path / "export.zip"
    build_collection_zip(spec, cid, out, frozenset({secret}))
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read(".kb-collection/manifest.json"))
    assert "public.md" in names
    assert "secret.md" not in names  # excluded from the zip
    paths = {d["path"] for d in manifest["documents"]}
    assert paths == {"public.md"}  # and from the manifest
