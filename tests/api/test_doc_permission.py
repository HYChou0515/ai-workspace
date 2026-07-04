"""#308 — per-doc permission override.

A SourceDoc may carry its OWN ``Permission`` that TIGHTENS (never loosens) the
read access it inherits from its parent collection (#303). Effective read =
collection-allows AND doc-override-allows. Only the collection owner (+ a
superuser) may set it; the doc uploader gets no special read right.

P1 covers the model + index registration only: the field exists, defaults to "no
override", the collection carries the ``has_doc_overrides`` short-circuit counter,
and the doc's own ``permission.visibility`` is a queryable index.
"""

from specstar import QB, SpecStar
from specstar.types import Binary

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, SourceDoc


def _mk_collection(spec: SpecStar, *, by: str = "bob", name: str = "c") -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using(by):
        return crm.create(Collection(name=name)).resource_id


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
        r.info.resource_id
        for r in drm.list_resources((QB["permission.visibility"] == "restricted").build())
    ]
    assert hits == [overridden_id]
