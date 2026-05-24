from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _client() -> TestClient:
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app)


def test_create_and_list_collections():
    client = _client()
    cid = client.post("/kb/collections", json={"name": "HR", "description": "policies"}).json()[
        "resource_id"
    ]
    listed = client.get("/kb/collections").json()
    match = next(c for c in listed if c["resource_id"] == cid)
    assert match["name"] == "HR"
    assert match["description"] == "policies"


def _new_collection(client: TestClient) -> str:
    return client.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]


def test_upload_document_and_list():
    client = _client()
    cid = _new_collection(client)
    files = {"file": ("guide.md", b"# Guide\none two three", "text/markdown")}
    r = client.post(f"/kb/collections/{cid}/documents", files=files)
    assert r.status_code == 200
    assert r.json()["document_ids"] == [f"{cid}/default-user/guide.md"]

    docs = client.get(f"/kb/collections/{cid}/documents").json()
    match = next(d for d in docs if d["resource_id"] == f"{cid}/default-user/guide.md")
    assert match["path"] == "guide.md"
    assert match["content_type"] in ("text/plain", "text/markdown")
    assert match["created_by"] == "default-user"  # specstar audit meta


def test_folder_upload_preserves_relative_path():
    # a folder upload sends each file with its relative path as the filename;
    # the doc id + path preserve that structure (handled like an archive member)
    client = _client()
    cid = _new_collection(client)
    files = {"file": ("manuals/reflow/guide.md", b"# Guide\nzone three", "text/markdown")}
    r = client.post(f"/kb/collections/{cid}/documents", files=files)
    assert r.json()["document_ids"] == [f"{cid}/default-user/manuals/reflow/guide.md"]
    docs = client.get(f"/kb/collections/{cid}/documents").json()
    assert any(d["path"] == "manuals/reflow/guide.md" for d in docs)


def test_render_document_rewrites_crossrefs_and_returns_markdown():
    import io
    import zipfile

    client = _client()
    cid = _new_collection(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("index.md", "See [Foo](./foo.md) and [Gone](./gone.md).")
        z.writestr("foo.md", "# Foo\nbody")
    client.post(
        f"/kb/collections/{cid}/documents",
        files={"file": ("docs.zip", buf.getvalue(), "application/zip")},
    )

    body = client.get(f"/kb/documents/{cid}/default-user/index.md").json()
    assert body["filename"] == "index.md"
    assert f"kb://doc/{cid}/default-user/foo.md" in body["markdown"]  # existing sibling → rewritten
    assert "[Gone](./gone.md)" in body["markdown"]  # missing → left as-is


def test_render_missing_document_404s():
    client = _client()
    cid = _new_collection(client)
    assert client.get(f"/kb/documents/{cid}/default-user/nope.md").status_code == 404
