"""#607 — the KB document- and wiki-management routes must authorize.

`GET /kb/documents` has always gated reads on the collection's `read_content`,
but the MUTATING doc routes (delete / move / reindex / guidance) and every wiki
route except `write_wiki_page` shipped with NO gate at all: any logged-in user
who could name a `doc_id` (the id is the deterministic `encode_doc_id`) or a
`collection_id` could delete/move/rewrite documents and wikis in collections
they cannot even see. These tests pin the gate onto each:

  * doc mutations      → `edit_content` (mirrors the collection-level reindex/sync)
  * wiki reads         → `read_content` (mirrors reading a document)
  * wiki mutations     → `edit_content`

with the same 404-hides-existence / 403-in-scope-but-unauthorized shape the read
routes already use, and the human-superuser bypass intact.
"""

import datetime as dt

import msgspec
import pytest
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection

from ._client import TestClient


def _ingestor(spec: SpecStar) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    return Ingestor(spec, pipeline=build_doc_pipeline(embedder=embedder), embedder=embedder)


def _client_and_spec(
    holder: dict[str, str], *, superusers: frozenset[str] = frozenset()
) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=__import__("workspace_app.sandbox.mock", fromlist=["MockSandbox"]).MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        get_user_id=lambda: holder["id"],
        superusers=superusers,  # MUST match make_spec's set (the route-guard's source)
    )
    return TestClient(app), spec


def _set_permission(spec: SpecStar, cid: str, permission: Permission, *, by: str = "bob") -> None:
    rm = spec.get_resource_manager(Collection)
    coll = rm.get(cid).data
    assert isinstance(coll, Collection)
    with rm.using(by, dt.datetime.now(dt.UTC)):
        rm.update(cid, msgspec.structs.replace(coll, permission=permission))


def _bobs_doc(
    holder: dict[str, str], *, superusers: frozenset[str] = frozenset()
) -> tuple[TestClient, SpecStar, str, str]:
    """A doc owned by bob in a fresh collection. Returns (client, spec, cid, doc_id)."""
    client, spec = _client_and_spec(holder, superusers=superusers)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    doc_id = _ingestor(spec).store(collection_id=cid, user="bob", filename="a.md", data=b"hello")[0]
    return client, spec, cid, doc_id


# --- document mutations: edit_content --------------------------------------


def test_delete_document_requires_edit_content():
    holder = {"id": "bob"}
    client, spec, cid, doc_id = _bobs_doc(holder)
    # alice may READ but not edit → the destructive route is 403, and the doc
    # survives (proven by bob still deleting it below).
    _set_permission(spec, cid, Permission(visibility="restricted", read_content=["user:alice"]))
    holder["id"] = "alice"
    assert client.delete(f"/kb/documents?id={doc_id}").status_code == 403
    holder["id"] = "bob"
    assert client.delete(f"/kb/documents?id={doc_id}").status_code == 200


def test_delete_document_in_a_private_collection_is_404_for_a_stranger():
    holder = {"id": "bob"}
    client, spec, cid, doc_id = _bobs_doc(holder)
    _set_permission(spec, cid, Permission(visibility="private"))
    holder["id"] = "mallory"
    # existence is hidden — a guessed doc_id gets the same 404 as an unknown one.
    assert client.delete(f"/kb/documents?id={doc_id}").status_code == 404


def test_move_document_requires_edit_content():
    holder = {"id": "bob"}
    client, spec, cid, doc_id = _bobs_doc(holder)
    _set_permission(spec, cid, Permission(visibility="restricted", read_content=["user:alice"]))
    holder["id"] = "alice"
    assert client.post(f"/kb/documents/move?id={doc_id}&to=b.md").status_code == 403
    holder["id"] = "bob"
    assert client.post(f"/kb/documents/move?id={doc_id}&to=b.md").status_code == 200


def test_reindex_document_requires_edit_content():
    holder = {"id": "bob"}
    client, spec, cid, doc_id = _bobs_doc(holder)
    _set_permission(spec, cid, Permission(visibility="restricted", read_content=["user:alice"]))
    holder["id"] = "alice"
    assert client.post(f"/kb/documents/reindex?id={doc_id}").status_code == 403
    holder["id"] = "bob"
    assert client.post(f"/kb/documents/reindex?id={doc_id}").status_code == 200


def test_set_guidance_requires_edit_content():
    holder = {"id": "bob"}
    client, spec, cid, doc_id = _bobs_doc(holder)
    _set_permission(spec, cid, Permission(visibility="restricted", read_content=["user:alice"]))
    holder["id"] = "alice"
    r = client.post(f"/kb/documents/guidance?id={doc_id}", json={"guidance": "x"})
    assert r.status_code == 403
    holder["id"] = "bob"
    r = client.post(f"/kb/documents/guidance?id={doc_id}", json={"guidance": "x"})
    assert r.status_code == 200


def test_superuser_manages_a_doc_in_a_private_collection():
    holder = {"id": "bob"}
    client, spec, cid, doc_id = _bobs_doc(holder, superusers=frozenset({"root"}))
    _set_permission(spec, cid, Permission(visibility="private"))
    holder["id"] = "root"
    # a human superuser bypasses (authorize.py step 2), even on a private collection.
    assert client.post(f"/kb/documents/reindex?id={doc_id}").status_code == 200
    assert client.delete(f"/kb/documents?id={doc_id}").status_code == 200


# --- wiki routes -----------------------------------------------------------

WIKI_READS = [
    ("get", "/kb/collections/{cid}/wiki"),
    ("get", "/kb/collections/{cid}/wiki/status"),
]

WIKI_MUTATIONS = [
    ("post", "/kb/collections/{cid}/wiki/move?from=A.md&to=B.md", None),
    ("delete", "/kb/collections/{cid}/wiki/page?path=A.md", None),
    ("post", "/kb/collections/{cid}/wiki/rebuild", None),
    ("post", "/kb/collections/{cid}/wiki/reflect", None),
    ("delete", "/kb/collections/{cid}/wiki", None),
    ("post", "/kb/collections/{cid}/wiki/corrections", {"instruction": "fix it"}),
]


@pytest.mark.parametrize("method,path", WIKI_READS)
def test_wiki_reads_require_read_content(method: str, path: str):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    _set_permission(spec, cid, Permission(visibility="private"))
    url = path.format(cid=cid)
    holder["id"] = "mallory"
    # a stranger to a private collection cannot read its wiki — 404 hides it.
    assert getattr(client, method)(url).status_code == 404
    holder["id"] = "bob"
    # the owner is not denied (200 or a benign empty result, never an auth error).
    assert getattr(client, method)(url).status_code not in (401, 403, 404)


def test_get_wiki_page_requires_read_content():
    """A written page: the owner reads it (200), a private-collection stranger is
    404 (hidden by the gate, not merely a missing page)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    page = f"/kb/collections/{cid}/wiki/page?path=Home.md"
    assert client.put(page, content=b"# Home").status_code == 200
    _set_permission(spec, cid, Permission(visibility="private"))
    assert client.get(page).status_code == 200  # bob (owner) reads it
    holder["id"] = "mallory"
    assert client.get(page).status_code == 404  # hidden, not "missing page"


@pytest.mark.parametrize("method,path,body", WIKI_MUTATIONS)
def test_wiki_mutations_require_edit_content(method: str, path: str, body: dict | None):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    # alice may READ but not edit; a private-collection stranger is hidden entirely.
    _set_permission(spec, cid, Permission(visibility="restricted", read_content=["user:alice"]))
    url = path.format(cid=cid)
    kw = {"json": body} if body is not None else {}
    holder["id"] = "alice"
    assert getattr(client, method)(url, **kw).status_code == 403
    holder["id"] = "mallory"
    _set_permission(spec, cid, Permission(visibility="private"))
    assert getattr(client, method)(url, **kw).status_code == 404
