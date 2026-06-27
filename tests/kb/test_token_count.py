"""#88: a SourceDoc carries a ``token_count`` derived from its extracted text,
so the collection grid can sum a chunk-based token estimate instead of the
raw-blob ``bytes / 4`` heuristic. Covers the index-time compute and the migrate
backfill of pre-existing rows."""

from specstar import BackendBinding, BackendConfig, ConnectionProfile, SpecStar
from specstar.types import Binary

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.tokens import count_tokens
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, SourceDoc


def _disk_backend(root) -> BackendConfig:
    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


def _new_collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def test_indexing_stores_token_count_from_extracted_text(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
) -> None:
    cid = _new_collection(spec)
    ingestor = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = "# 指南\n資料科學 one two three four".encode()
    (doc_id,) = ingestor.ingest(collection_id=cid, user="alice", filename="g.md", data=data)

    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    assert isinstance(doc, SourceDoc)
    assert doc.text is not None
    assert doc.token_count == count_tokens(doc.text)
    assert doc.token_count > 0


def test_migrate_backfills_token_count_from_text(tmp_path) -> None:
    # A SourceDoc written before #88 (no token_count index, version `None`) has
    # token_count == 0 (the default). The operator backfills it WITHOUT
    # re-parsing/re-embedding by running migrate, which the #88 schema step
    # recomputes from the already-stored `text`.
    backend = _disk_backend(tmp_path)
    spec_old = SpecStar()
    spec_old.configure(default_user="u", backend=backend)
    spec_old.add_model(Collection)
    spec_old.add_model(SourceDoc, indexed_fields=["collection_id"])  # no Schema ⇒ version None
    text = "資料科學 hello world 測試報告"
    rid = (
        spec_old.get_resource_manager(SourceDoc)
        .create(SourceDoc(collection_id="c1", path="g.md", content=Binary(data=b"x"), text=text))
        .resource_id
    )
    assert spec_old.get_resource_manager(SourceDoc).get(rid).data.token_count == 0

    # New code (v4 schema + backfill step) on the SAME store.
    drm = make_spec(default_user="u", backend=backend).get_resource_manager(SourceDoc)
    drm.migrate(rid)  # operator backfill: POST /source-doc/migrate/execute
    backfilled = drm.get(rid).data
    assert isinstance(backfilled, SourceDoc)
    assert backfilled.token_count == count_tokens(text) > 0
