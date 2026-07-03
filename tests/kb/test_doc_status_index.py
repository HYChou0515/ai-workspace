"""#395: the document list must be servable from search metas alone — no
per-row data-blob fetch. That requires every field the list renders to live in
``indexed_data``: ``status`` / ``status_detail`` / ``content.content_type`` on
SourceDoc (Schema v5 → v6), and ``collection_id`` + unit progress on IndexRun
(the per-page ``IndexRunStore.get`` N+1 becomes one collection-scoped metas
search). Covers the new indexes on fresh writes and the migrate backfill for
pre-#395 rows."""

from specstar import QB, BackendBinding, BackendConfig, ConnectionProfile, SpecStar
from specstar.types import Binary

from workspace_app.kb.index_run import IndexRunStore
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, IndexRun, SourceDoc


def _new_collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _doc(cid: str, path: str, status: str) -> SourceDoc:
    return SourceDoc(
        collection_id=cid,
        path=path,
        content=Binary(data=b"x", content_type="text/markdown"),
        status=status,
        status_detail="boom" if status == "error" else "",
    )


def test_status_is_queryable(spec: SpecStar) -> None:
    cid = _new_collection(spec)
    rm = spec.get_resource_manager(SourceDoc)
    rm.create(_doc(cid, "ok.md", "ready"))
    errored = rm.create(_doc(cid, "bad.md", "error")).resource_id

    q = ((QB["collection_id"] == cid) & (QB["status"] == "error")).build()
    metas = rm.search_resources(q)
    assert [m.resource_id for m in metas] == [errored]


def _disk_backend(root) -> BackendConfig:
    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


def test_migrate_backfills_status_index(tmp_path) -> None:
    # A pre-#395 SourceDoc (version `None`, status not indexed) is invisible to
    # a status-filtered search until the operator runs the migrate route, which
    # chains the row up to v6 and re-extracts its indexed_data.
    backend = _disk_backend(tmp_path)
    spec_old = SpecStar()
    spec_old.configure(default_user="u", backend=backend)
    spec_old.add_model(Collection)
    spec_old.add_model(SourceDoc, indexed_fields=["collection_id"])  # no Schema ⇒ version None
    rid = spec_old.get_resource_manager(SourceDoc).create(_doc("c1", "bad.md", "error")).resource_id

    drm = make_spec(default_user="u", backend=backend).get_resource_manager(SourceDoc)
    q = ((QB["collection_id"] == "c1") & (QB["status"] == "error")).build()
    assert drm.search_resources(q) == []

    drm.migrate(rid)  # operator backfill: POST /source-doc/migrate/execute
    assert [m.resource_id for m in drm.search_resources(q)] == [rid]


def test_pre_395_index_run_rows_read_as_zero_progress(tmp_path) -> None:
    # An IndexRun written before the #395 unit indexes has no units_* in its
    # indexed_data — the metas read degrades to a 0/0 bar (no Schema/migrate
    # ceremony for short-lived join state; the next fan-out re-seeds it fresh).
    backend = _disk_backend(tmp_path)
    old = SpecStar()
    old.configure(default_user="u", backend=backend)
    old.add_model(IndexRun, indexed_fields=["status"])  # the pre-#395 registration
    old.get_resource_manager(IndexRun).create_or_update(
        "doc-1", IndexRun(doc_id="doc-1", collection_id="coll-1", total=2, units_total=8)
    )

    rm = make_spec(default_user="u", backend=backend).get_resource_manager(IndexRun)
    metas = rm.search_resources((QB["status"] == "running").build())
    assert len(metas) == 1
    indexed = metas[0].indexed_data
    assert isinstance(indexed, dict)
    assert "units_done" not in indexed  # the degradation the API folds to 0/0


def test_index_run_progress_readable_from_metas(spec: SpecStar) -> None:
    # #395 Batch A: one collection-scoped metas search replaces the per-doc
    # `IndexRunStore.get` point-reads in the list loop — so a run's unit
    # progress must ride `indexed_data`, live-updated by the CAS writes.
    runs = IndexRunStore(spec)
    runs.start("doc-1", "coll-1", total=2, units_total=8)
    runs.mark_done("doc-1", 0, batch_units=3)
    runs.start("doc-other", "coll-2", total=1, units_total=4)

    rm = spec.get_resource_manager(IndexRun)
    metas = rm.search_resources((QB["collection_id"] == "coll-1").build())
    assert len(metas) == 1
    indexed = metas[0].indexed_data
    assert isinstance(indexed, dict)
    assert indexed["units_done"] == 3
    assert indexed["units_total"] == 8
