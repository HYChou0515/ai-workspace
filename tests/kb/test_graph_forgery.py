"""#534 — a graph row cannot claim a permission its collection does not grant.

The read side is driven entirely by the mirror each evidence row carries, so
anyone who can WRITE a row with a mirror of their choosing can publish a fact of
their choosing to everyone. The auto-CRUD create route is open to any signed-in
caller, which is the whole attack: POST a claim into someone's private
collection, stamp the mirror "public", and it is readable by all.

The gate deliberately does not ask WHO is writing. The extraction job runs as an
ordinary user and must keep working, and any rule phrased around identity would
have to name it. It asks instead whether the mirror TELLS THE TRUTH about the
collection it names — which the extractor satisfies for free, because it copies
the mirror from the document rather than composing one.
"""

from __future__ import annotations

import pytest

from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim, GraphMention
from workspace_app.resources.kb import Collection


def _private_collection(spec) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using("bob"):
        return rm.create(
            Collection(name="secret", permission=Permission(visibility="private"))
        ).resource_id


def _claim(cid: str, **mirror) -> GraphClaim:
    return GraphClaim(
        collection_id=cid,
        source_doc_id="deck-A",
        norm_metric="revenue",
        metric="Revenue",
        value="999M",
        **mirror,
    )


def test_a_forged_public_mirror_is_refused():
    spec = make_spec(default_user=lambda: "mallory")
    cid = _private_collection(spec)
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("mallory"), pytest.raises(Exception):  # noqa: B017 — the backend's own denial
        rm.create(_claim(cid, collection_visibility="public", collection_created_by="mallory"))


def test_a_truthful_mirror_is_written_by_anyone():
    """The extractor is an ordinary user and must keep working. It never composes
    a mirror — it copies the document's — so it passes without being named."""
    spec = make_spec(default_user=lambda: "worker")
    cid = _private_collection(spec)
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("worker"):
        rid = rm.create(
            _claim(cid, collection_visibility="private", collection_created_by="bob")
        ).resource_id
    assert rm.get(rid).data is not None


def test_the_same_gate_covers_mentions():
    spec = make_spec(default_user=lambda: "mallory")
    cid = _private_collection(spec)
    rm = spec.get_resource_manager(GraphMention)
    with rm.using("mallory"), pytest.raises(Exception):  # noqa: B017
        rm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id="deck-A",
                surface="回焊爐",
                collection_visibility="public",
                collection_created_by="mallory",
            )
        )


def test_a_row_with_no_mirror_is_still_allowed():
    """An unwritten mirror is not a lie — and the read side already hides such a
    row from everyone. Refusing it here would break the fail-closed default that
    lets a writer forget the mirror and lose rows loudly."""
    spec = make_spec(default_user=lambda: "worker")
    cid = _private_collection(spec)
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("worker"):
        assert rm.create(_claim(cid)).resource_id
