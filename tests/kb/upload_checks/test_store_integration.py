"""Ingestor.store() runs the upload checks before creating any SourceDoc
(#325).

The gate is synchronous and lives at the top of ``store`` (before archive
expansion, before the blob is persisted), so a refused file leaves NO
doc behind — the upload is rejected outright, not stored-then-errored.
"""

from __future__ import annotations

import pytest
from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.upload_checks import (
    IUploadCheck,
    UploadCheckRegistry,
    UploadRejected,
    UploadRejection,
)
from workspace_app.resources.kb import Collection, SourceDoc

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _cid(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _doc_count(spec: SpecStar) -> int:
    return len(spec.get_resource_manager(SourceDoc).list_resources(QB.all().build()))


def test_store_rejects_an_encrypted_office_file_and_persists_nothing(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _cid(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    with pytest.raises(UploadRejected) as exc:
        ing.store(collection_id=cid, user="alice", filename="deck.pptx", data=OLE2 + b"blob")
    assert exc.value.rejection.check_id == "office_encryption"
    assert _doc_count(spec) == 0  # nothing stored


def test_store_accepts_a_normal_upload_unchanged(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _cid(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ids = ing.store(collection_id=cid, user="alice", filename="guide.md", data=b"# hi\nbody")
    assert len(ids) == 1
    assert _doc_count(spec) == 1


def test_injected_registry_overrides_the_default_bundle(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """An operator can swap in their own checks — here one that blocks
    every .md, proving the default bundle is replaceable, not hard-wired."""

    class _BlockMd(IUploadCheck):
        @property
        def id(self) -> str:
            return "no_md"

        def applies(self, *, filename: str, mime: str) -> bool:
            return filename.endswith(".md")

        def inspect(self, data: bytes) -> UploadRejection | None:
            return UploadRejection(check_id="no_md", reason_code="x", message_key="k")

    registry = UploadCheckRegistry().register(_BlockMd())
    ing = Ingestor(spec, chunker=chunker, embedder=embedder, upload_checks=registry)
    with pytest.raises(UploadRejected):
        ing.store(collection_id=_cid(spec), user="a", filename="x.md", data=b"hello")


def test_upload_check_hints_exposes_the_browser_runnable_descriptors(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    hints = ing.upload_check_hints()
    assert [h.id for h in hints] == ["office_encryption"]
