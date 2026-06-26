"""Issue #263: provenance is indexed so a chunk can be fetched by its
structural location (page range / sheet) — a deterministic WHERE that composes
with vector retrieval, not a separate semantic search."""

from __future__ import annotations

from specstar import QB
from specstar.types import Binary

from workspace_app.resources.kb import EMBED_DIM, DocChunk, SourceDoc


def _add_chunk(spec, *, doc="d1", coll="c1", seq=0, provenance):
    rm = spec.get_resource_manager(DocChunk)
    rm.create(
        DocChunk(
            collection_id=coll,
            source_doc_id=doc,
            seq=seq,
            start=0,
            end=1,
            text=f"chunk {seq}",
            provenance=provenance,
            embedding=[0.0] * EMBED_DIM,
        )
    )


def test_docchunk_filterable_by_page_range(spec):
    for p in (10, 30, 60, 90, 120):
        _add_chunk(spec, seq=p, provenance={"page": p})
    rm = spec.get_resource_manager(DocChunk)
    q = (QB["source_doc_id"] == "d1") & QB["page"].between(30, 90)
    pages = sorted(r.data.provenance["page"] for r in rm.list_resources(q.build()))
    assert pages == [30, 60, 90]


def test_docchunk_filterable_by_sheet_exact(spec):
    for i, name in enumerate(["Summary", "Q3", "Summary", "Raw"]):
        _add_chunk(spec, seq=i, provenance={"sheet": name})
    rm = spec.get_resource_manager(DocChunk)
    q = (QB["source_doc_id"] == "d1") & (QB["sheet"] == "Summary")
    seqs = sorted(r.data.seq for r in rm.list_resources(q.build()))
    assert seqs == [0, 2]


def test_docchunk_participates_in_migrate_backfill(spec):
    # #263: existing chunks (written before the provenance indexes existed) are
    # backfilled by the operator running migrate, which re-extracts indexed_data
    # WITHOUT re-parsing / re-embedding. So DocChunk must have a migration
    # configured (a Schema), else `migrate` raises "Migration is not set".
    _add_chunk(spec, seq=1, provenance={"page": 7})
    rm = spec.get_resource_manager(DocChunk)
    [rid] = [r.info.resource_id for r in rm.list_resources((QB["source_doc_id"] == "d1").build())]
    rm.migrate(rid)  # must not raise


def _add_doc(spec, *, path, coll="c1"):
    rm = spec.get_resource_manager(SourceDoc)
    rm.create(SourceDoc(collection_id=coll, path=path, content=Binary(data=b"x")))


def test_sourcedoc_resolvable_by_path_exact_and_basename(spec):
    for p in ("reports/Q3.xlsx", "Q3-old.xlsx", "notes.md"):
        _add_doc(spec, path=p)
    rm = spec.get_resource_manager(SourceDoc)

    exact = (QB["collection_id"] == "c1") & (QB["path"] == "reports/Q3.xlsx")
    assert sorted(r.data.path for r in rm.list_resources(exact.build())) == ["reports/Q3.xlsx"]

    # A user who types just "Q3" should reach both Q3 files via basename match.
    basename = (QB["collection_id"] == "c1") & QB["path"].contains("Q3")
    assert sorted(r.data.path for r in rm.list_resources(basename.build())) == [
        "Q3-old.xlsx",
        "reports/Q3.xlsx",
    ]
