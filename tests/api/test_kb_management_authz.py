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

# read_content tier: reading the wiki, polling status, and REPORTING an error
# (submit/draft) — the fix is applied by the trusted background corrector, so
# reporting is a reader-tier action that matches the `request_wiki_update` tool.
# NOTE: corrections/DRAFT is gated the same way but tested separately — its
# allowed path calls a real LLM (no Ollama in CI), so only its denials (which
# short-circuit at the gate, before the LLM) are asserted below.
WIKI_READ_TIER = [
    ("get", "/kb/collections/{cid}/wiki", None),
    ("get", "/kb/collections/{cid}/wiki/status", None),
    ("post", "/kb/collections/{cid}/wiki/corrections", {"instruction": "fix it"}),
]

WIKI_EDIT_TIER = [
    ("post", "/kb/collections/{cid}/wiki/move?from=A.md&to=B.md", None),
    ("delete", "/kb/collections/{cid}/wiki/page?path=A.md", None),
    ("post", "/kb/collections/{cid}/wiki/rebuild", None),
    ("post", "/kb/collections/{cid}/wiki/reflect", None),
    ("delete", "/kb/collections/{cid}/wiki", None),
]


@pytest.mark.parametrize("method,path,body", WIKI_READ_TIER)
def test_wiki_read_tier_requires_read_content(method: str, path: str, body: dict | None):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    # alice HAS read_content; carol sees it exists (read_meta) but not its content.
    _set_permission(
        spec,
        cid,
        Permission(visibility="restricted", read_meta=["user:carol"], read_content=["user:alice"]),
    )
    url = path.format(cid=cid)
    kw = {"json": body} if body is not None else {}
    holder["id"] = "alice"
    # past the gate — may 200 or a benign 400 (no wiki), but never an auth denial.
    assert getattr(client, method)(url, **kw).status_code != 403
    holder["id"] = "carol"
    # read_meta is NOT enough: read_content is required (proves the verb, not just
    # existence). A restricted collection is existence-visible, so this is 403.
    assert getattr(client, method)(url, **kw).status_code == 403
    holder["id"] = "mallory"
    _set_permission(spec, cid, Permission(visibility="private"))
    assert getattr(client, method)(url, **kw).status_code == 404


def test_wiki_corrections_draft_requires_read_content():
    """corrections/DRAFT is read_content-gated like the rest of the read tier, but
    its allowed path runs a real LLM (unavailable in CI) — so assert only the
    denials, which short-circuit at the gate before any LLM call."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    url = f"/kb/collections/{cid}/wiki/corrections/draft"
    body = {"question": "q", "answer": "a"}
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:carol"]))
    holder["id"] = "carol"
    assert client.post(url, json=body).status_code == 403  # read_meta is not read_content
    holder["id"] = "mallory"
    _set_permission(spec, cid, Permission(visibility="private"))
    assert client.post(url, json=body).status_code == 404


def test_get_wiki_page_requires_read_content():
    """A written page: the owner reads it (200), a read_meta-only member is 403
    (verb required), a private-collection stranger is 404 (hidden, not missing)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    page = f"/kb/collections/{cid}/wiki/page?path=Home.md"
    assert client.put(page, content=b"# Home").status_code == 200
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:carol"]))
    assert client.get(page).status_code == 200  # bob (owner) reads it
    holder["id"] = "carol"
    assert client.get(page).status_code == 403  # sees it exists, not its content
    holder["id"] = "mallory"
    _set_permission(spec, cid, Permission(visibility="private"))
    assert client.get(page).status_code == 404  # hidden, not "missing page"


@pytest.mark.parametrize("method,path,body", WIKI_EDIT_TIER)
def test_wiki_mutations_require_edit_content(method: str, path: str, body: dict | None):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = client.post("/kb/collections", json={"name": "c"}).json()["resource_id"]
    # alice may READ but not edit; a private-collection stranger is hidden entirely.
    _set_permission(spec, cid, Permission(visibility="restricted", read_content=["user:alice"]))
    url = path.format(cid=cid)
    kw = {"json": body} if body is not None else {}
    holder["id"] = "alice"
    assert getattr(client, method)(url, **kw).status_code == 403  # read_content is not edit
    holder["id"] = "bob"
    # the OWNER is never denied by the gate (may 404/400 for business reasons — a
    # missing page, no wiki — but never a 403). Proves the gate isn't over-strict.
    assert getattr(client, method)(url, **kw).status_code != 403
    holder["id"] = "mallory"
    _set_permission(spec, cid, Permission(visibility="private"))
    assert getattr(client, method)(url, **kw).status_code == 404


# --- findability + documents-status: read a doc's content / a collection's -----
#     indexing state — same read_content gate as render_document / the list.

FINDABILITY = [
    ("/kb/findability/probe", "doc"),
    ("/kb/findability/answer", "doc"),
]


@pytest.mark.parametrize("url,kind", FINDABILITY)
def test_findability_endpoints_require_read_content(url: str, kind: str):
    holder = {"id": "bob"}
    client, spec, cid, doc_id = _bobs_doc(holder)
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:carol"]))
    body = {"doc_id": doc_id, "question": "what is this"}
    # a read_meta-only member sees the doc exists but cannot distil its content —
    # blocked at the gate, before any retrieval / LLM runs.
    holder["id"] = "carol"
    assert client.post(url, json=body).status_code == 403
    holder["id"] = "mallory"
    _set_permission(spec, cid, Permission(visibility="private"))
    assert client.post(url, json=body).status_code == 404


def test_documents_status_requires_read_content():
    holder = {"id": "bob"}
    client, spec, cid, _doc_id = _bobs_doc(holder)
    url = f"/kb/collections/{cid}/documents/status"
    _set_permission(spec, cid, Permission(visibility="restricted", read_meta=["user:carol"]))
    holder["id"] = "bob"
    assert client.get(url).status_code == 200  # owner sees the indexing state
    holder["id"] = "carol"
    # read_meta only: the counts / latest activity / per-run doc paths stay hidden.
    assert client.get(url).status_code == 403
    holder["id"] = "mallory"
    _set_permission(spec, cid, Permission(visibility="private"))
    assert client.get(url).status_code == 404
