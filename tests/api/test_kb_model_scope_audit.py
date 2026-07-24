"""Which KB-content models are readable by anyone over the auto-CRUD routes?

specstar's permission default is AllowAll: a model registered with no
access_scope returns every row to any caller (`GET /{model}` is generated for
every model). #534 states this explicitly for GraphClaim, which is why the graph
family carries a permission mirror. This test asks the same question of every
model that holds knowledge-base CONTENT, so the answer is a fact rather than an
assumption — and so anything added later has to answer it too.
"""

from __future__ import annotations

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard, DocQuestion
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

SECRET = "SECRET-BODY-12345"


def _client(who: dict[str, str]):
    spec = make_spec(default_user=lambda: who["id"])
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="secret", permission=Permission(visibility="private"))
        ).resource_id
    cardrm = spec.get_resource_manager(ContextCard)
    with cardrm.using("bob"):
        cardrm.create(
            ContextCard(
                collection_id=cid,
                keys=["回焊爐"],
                norm_keys=derive_norm_keys(["回焊爐"]),
                title="回焊爐",
                body=SECRET,
            )
        )
    qrm = spec.get_resource_manager(DocQuestion)
    with qrm.using("bob"):
        qrm.create(
            DocQuestion(
                collection_id=cid,
                kind="term",
                term="回焊爐",
                norm_key="回焊爐",
                question_text=SECRET,
                source_doc_id="deck-A",
            )
        )
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: who["id"],
    )
    return TestClient(app)


def test_collections_themselves_are_scoped():
    """The baseline this audit measures against."""
    who = {"id": "bob"}
    client = _client(who)
    assert any(c["data"]["name"] == "secret" for c in client.get("/collection").json())
    who["id"] = "alice"
    assert not any(c["data"]["name"] == "secret" for c in client.get("/collection").json())


@pytest.mark.parametrize("model", ["context-card", "doc-question"])
@pytest.mark.xfail(
    reason="KNOWN HOLE: these models carry a private collection's content but "
    "register no access_scope, so the generated GET /{model} route serves them "
    "to any caller. Tracked separately; this test turns green when fixed.",
    strict=True,
)
def test_content_models_do_not_leak_to_a_stranger(model: str):
    who = {"id": "bob"}
    client = _client(who)
    who["id"] = "alice"
    resp = client.get(f"/{model}")
    assert resp.status_code != 200 or SECRET not in str(resp.json())
