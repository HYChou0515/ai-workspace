"""Issue #105: a SourceDoc carries an AI quality assessment (a holistic 0–100
``quality_score`` + per-dimension ``quality_breakdown`` + ``quality_rationale``).
The score is indexed so the document list can sort by quality and the retriever
can batch-load candidate doc scores. Un-scored docs default to ``None`` (neutral —
never penalised)."""

from __future__ import annotations

from specstar import QB
from specstar.types import Binary

from workspace_app.resources.kb import Collection, SourceDoc


def _add_doc(spec, *, path, coll="c1", score=None, breakdown=None, rationale=""):
    rm = spec.get_resource_manager(SourceDoc)
    rm.create(
        SourceDoc(
            collection_id=coll,
            path=path,
            content=Binary(data=b"x"),
            quality_score=score,
            quality_breakdown=breakdown or {},
            quality_rationale=rationale,
        )
    )


def test_sourcedoc_defaults_to_unscored(spec):
    rm = spec.get_resource_manager(SourceDoc)
    rm.create(SourceDoc(collection_id="c1", path="a.md", content=Binary(data=b"x")))
    [doc] = [r.data for r in rm.list_resources((QB["collection_id"] == "c1").build())]
    assert doc.quality_score is None
    assert doc.quality_breakdown == {}
    assert doc.quality_rationale == ""


def test_sourcedoc_filterable_by_quality_score(spec):
    _add_doc(spec, path="good.md", score=80)
    _add_doc(spec, path="bad.md", score=20)
    _add_doc(spec, path="pending.md")  # un-scored
    rm = spec.get_resource_manager(SourceDoc)
    q = (QB["collection_id"] == "c1") & (QB["quality_score"] < 50)
    assert sorted(r.data.path for r in rm.list_resources(q.build())) == ["bad.md"]


def test_sourcedoc_sortable_by_quality_worst_first(spec):
    # The document list's "sort by quality" surfaces the worst docs — an
    # ascending sort on the index (worst score first).
    for path, score in [("mid.md", 55), ("worst.md", 12), ("best.md", 91)]:
        _add_doc(spec, path=path, score=score)
    rm = spec.get_resource_manager(SourceDoc)
    q = (QB["collection_id"] == "c1") & (QB["quality_score"] >= 0)
    ordered = [r.data.path for r in rm.list_resources(q.sort(QB["quality_score"].asc()).build())]
    assert ordered == ["worst.md", "mid.md", "best.md"]


def test_collection_quality_rubric_round_trips(spec):
    # #105: the user's scoring criteria live on the Collection (non-indexed,
    # like the #90 wiki guidance). Empty default ⇒ the collection is not scored.
    rm = spec.get_resource_manager(Collection)
    plain = rm.create(Collection(name="Defects"))
    assert rm.get(plain.resource_id).data.quality_rubric == ""
    scored = rm.create(Collection(name="Specs", quality_rubric="Judge clarity and noise: 0-100."))
    assert rm.get(scored.resource_id).data.quality_rubric.startswith("Judge clarity")


def test_sourcedoc_participates_in_migrate_backfill(spec):
    # #105: docs written before the quality_score index existed are backfilled
    # when the operator runs migrate (re-extracting indexed_data, NO LLM). So
    # SourceDoc must have a Schema reaching v5, else `migrate` raises.
    _add_doc(spec, path="legacy.md")
    rm = spec.get_resource_manager(SourceDoc)
    [rid] = [r.info.resource_id for r in rm.list_resources((QB["collection_id"] == "c1").build())]
    rm.migrate(rid)  # must not raise
