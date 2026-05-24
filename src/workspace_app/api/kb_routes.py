"""KB (knowledge-base chatbot) HTTP routes — collections, document upload/list,
and the document render endpoint. Registered onto the app by `create_app`.
"""

from __future__ import annotations

import posixpath

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..kb.ingest import Ingestor, normalize_text
from ..kb.links import rewrite_md_links
from ..resources.kb import Collection, SourceDoc

_DEFAULT_USER = "default-user"  # v1: no auth; uploads are attributed to this user


class _CollectionBody(BaseModel):
    name: str
    description: str = ""


def register_kb_routes(app: FastAPI, spec: SpecStar, ingestor: Ingestor) -> None:
    @app.post("/kb/collections")
    async def create_collection(body: _CollectionBody) -> dict:
        rm = spec.get_resource_manager(Collection)
        rev = rm.create(Collection(name=body.name, description=body.description))
        return {"resource_id": rev.resource_id, "name": body.name, "description": body.description}

    @app.get("/kb/collections")
    async def list_collections() -> list[dict]:
        rm = spec.get_resource_manager(Collection)
        out: list[dict] = []
        for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            data = r.data
            assert isinstance(data, Collection)
            out.append(
                {
                    "resource_id": r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    "name": data.name,
                    "description": data.description,
                }
            )
        return out

    @app.post("/kb/collections/{collection_id}/documents")
    async def upload_document(
        collection_id: str,
        background: BackgroundTasks,
        file: UploadFile = File(...),  # noqa: B008
    ) -> dict:
        data = await file.read()
        # Store fast (doc appears as "indexing"); embed in the background so a
        # slow embedder doesn't block the upload response.
        ids = ingestor.store(
            collection_id=collection_id,
            user=_DEFAULT_USER,
            filename=file.filename or "upload",
            data=data,
        )
        for doc_id in ids:
            background.add_task(ingestor.index, doc_id)
        return {"document_ids": ids, "status": "indexing"}

    @app.get("/kb/collections/{collection_id}/documents")
    async def list_documents(collection_id: str) -> list[dict]:
        rm = spec.get_resource_manager(SourceDoc)
        out: list[dict] = []
        for r in rm.list_resources((QB["collection_id"] == collection_id).build()):
            data = r.data
            assert isinstance(data, SourceDoc)
            out.append(
                {
                    "resource_id": r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    "path": data.path,
                    "content_type": data.content.content_type,
                    "created_by": r.info.created_by,  # ty: ignore[unresolved-attribute]
                    "status": data.status,
                }
            )
        return out

    @app.get("/kb/documents/{doc_id:path}")
    async def render_document(doc_id: str) -> dict:
        rm = spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(doc, SourceDoc)
        raw = rm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)  # restore_binary populates the blob bytes
        text = normalize_text(raw.decode("utf-8", errors="replace"))
        _, user, path = doc_id.split("/", 2)

        def exists(rid: str) -> bool:
            try:
                rm.get(rid)
            except ResourceIDNotFoundError:
                return False
            return True

        markdown = rewrite_md_links(
            text, doc_path=path, collection_id=doc.collection_id, user=user, exists=exists
        )
        return {
            "filename": posixpath.basename(path),
            "collection_id": doc.collection_id,
            "markdown": markdown,
        }
