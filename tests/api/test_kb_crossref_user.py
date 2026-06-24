"""#41 cross-reference links must survive a non-default current user.

The KB mints each SourceDoc's id from `{collection}/{user}/{path}` and resolves
sibling cross-refs (`[B](./b.md)` → `kb://doc/{id}`) at render time using the
doc's `created_by`. If upload mints the id with a DIFFERENT user than specstar
stamps as `created_by`, the sibling lookup misses and the link is left relative
— which then 404s in the doc viewer. So the upload user and the owner user must
be the same `get_user_id`.
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _app(user_id: str):
    """App where BOTH created_by (specstar) and the access user follow `user_id`
    — the real wiring once an operator overrides get_user_id (e.g. from a cookie)."""
    spec = make_spec(default_user=lambda: user_id)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: user_id,
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=1),
    )
    return TestClient(app), spec


def test_crossref_link_rewritten_for_non_default_user():
    # A non-default current user (the operator set server.default_user / cookie).
    client, spec = _app("alice")
    cid = spec.get_resource_manager(Collection).create(Collection(name="docs")).resource_id

    # Upload a folder of two markdown files; A references its sibling B.
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("folder/b.md", b"# B\n\nthe other doc\n", "text/markdown")},
    )
    up = client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("folder/a.md", b"see more in [B](./b.md)\n", "text/markdown")},
    )
    a_id = up.json()["document_ids"][0]

    rendered = client.get(f"/kb/documents?id={a_id}").json()
    # The cross-ref must resolve to an in-app kb:// link, not stay relative.
    assert "kb://doc/" in rendered["markdown"]
    assert "./b.md" not in rendered["markdown"]
