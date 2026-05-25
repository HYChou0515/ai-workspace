"""Cited counts surface on the KB management endpoints (collections / documents
/ chunks), wired to the CitationEvent log."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import QB, SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.cited import record_citations
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources.kb import EMBED_DIM, Citation, DocChunk
from workspace_app.sandbox.mock import MockSandbox


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


def _app() -> tuple[TestClient, SpecStar]:
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
    return TestClient(app), spec


def test_cited_counts_surface_on_collections_documents_and_chunks():
    c, spec = _app()
    cid = c.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    files = {"file": ("guide.md", b"# Guide\none two three four five", "text/markdown")}
    docid = c.post(f"/kb/collections/{cid}/documents", files=files).json()["document_ids"][0]

    # before any citation
    doc = next(
        d for d in c.get(f"/kb/collections/{cid}/documents").json() if d["resource_id"] == docid
    )
    assert doc["chunks"] >= 1  # indexed (TestClient runs the bg task)
    assert doc["cited"] == 0
    assert next(x for x in c.get("/kb/collections").json() if x["resource_id"] == cid)["cited"] == 0

    # grab a real chunk id and record one citation of this doc (merging it)
    chunk = next(
        iter(
            spec.get_resource_manager(DocChunk).list_resources(
                (QB["source_doc_id"] == docid).build()
            )
        )
    )
    chunk_id = chunk.info.resource_id  # ty: ignore[unresolved-attribute]
    record_citations(
        spec,
        [
            Citation(
                marker=1,
                collection_id=cid,
                document_id=docid,
                filename="guide.md",
                start=0,
                end=1,
                source_chunk_ids=[chunk_id],
            )
        ],
        origin_kind="kb_chat",
        origin_id="chat",
        cited_by="u",
    )

    # after: the count surfaces at collection, doc, and chunk level
    assert next(x for x in c.get("/kb/collections").json() if x["resource_id"] == cid)["cited"] == 1
    doc = next(
        d for d in c.get(f"/kb/collections/{cid}/documents").json() if d["resource_id"] == docid
    )
    assert doc["cited"] == 1

    chunks = c.get("/kb/documents/chunks", params={"id": docid}).json()
    assert any(ch["chunk_id"] == chunk_id and ch["cited"] == 1 for ch in chunks)
    assert [ch["seq"] for ch in chunks] == sorted(ch["seq"] for ch in chunks)  # ordered by seq


def test_documents_carry_byte_size_and_update_time():
    c, _ = _app()
    cid = c.post("/kb/collections", json={"name": "kb"}).json()["resource_id"]
    body = b"# Guide\none two three four five"
    files = {"file": ("guide.md", body, "text/markdown")}
    docid = c.post(f"/kb/collections/{cid}/documents", files=files).json()["document_ids"][0]

    doc = next(
        d for d in c.get(f"/kb/collections/{cid}/documents").json() if d["resource_id"] == docid
    )
    # specstar computes the blob size on store — surfaced verbatim (bytes)
    assert doc["size"] == len(body)
    # updated_at is the resource's revision time, epoch ms
    assert isinstance(doc["updated_at"], int) and doc["updated_at"] > 0
