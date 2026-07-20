"""Topic Hub §5 — the collection set is a workspace file (`collections.json`);
`resolve_collection` turns a user-given id-or-name into the canonical {id, name}
the agent records there (resolve only, no write)."""

from __future__ import annotations

from workspace_app.kb.collections import (
    collection_ids_from_json,
    collection_tiers_from_json,
    resolve_collection,
    resolve_profile_collections,
)
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, SourceDoc


def _coll(spec, name: str) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def test_source_doc_numeric_aggregates_stay_pushdown_eligible():
    """The collections dashboard (`GET /kb/collections`) sums each collection's
    doc `content_size` + `token_count` via `ForeignAggregate(Sum(...))`. Those
    push down to a real GROUP BY only when the indexed field carries a declared
    numeric `field_type` (specstar #406/#407); drop it and the aggregate
    silently falls back to streaming every doc into Python — a prod-only perf
    regression no other test would catch. Guard it here."""
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(SourceDoc)
    # indexed_fields is on the concrete ResourceManager, not the interface ty
    # sees (same as exp_aggregate_by / event_handlers elsewhere).
    fields = rm.indexed_fields  # ty: ignore[unresolved-attribute]
    by_key = {(f.index_key or f.field_path): f for f in fields}
    assert by_key["content_size"].field_type is int
    assert by_key["token_count"].field_type is int


def test_collection_ids_from_json_extracts_ids_in_order():
    data = [{"id": "c1", "name": "Defects"}, {"id": "c2", "name": "Logs"}]
    assert collection_ids_from_json(data) == ["c1", "c2"]


def test_collection_ids_from_json_skips_malformed_entries():
    # a hand-edited file mustn't crash the turn: drop entries with no/blank id.
    data = [{"id": "c1", "name": "A"}, {"name": "no id"}, {"id": "", "name": "blank"}, "garbage"]
    assert collection_ids_from_json(data) == ["c1"]
    assert collection_ids_from_json("not a list") == []


def test_collection_tiers_from_json_groups_by_tier_ranked_ascending():
    # Sparse tier ints (0, 10, 20 — room to insert later) collapse to ranks 0,1,2.
    data = [
        {"id": "a", "name": "A", "tier": 0},
        {"id": "b", "name": "B", "tier": 0},
        {"id": "d", "name": "D", "tier": 20},
        {"id": "c", "name": "C", "tier": 10},
    ]
    # rank 0 = smallest tier (0) → [a, b] in file order; rank 1 = tier 10 → [c];
    # rank 2 = tier 20 → [d].
    assert collection_tiers_from_json(data) == [["a", "b"], ["c"], ["d"]]


def test_collection_tiers_from_json_defaults_missing_tier_to_zero():
    # A flat hand-edited / legacy file (no `tier`) is one tier — backward compatible.
    data = [{"id": "c1", "name": "A"}, {"id": "c2", "name": "B"}]
    assert collection_tiers_from_json(data) == [["c1", "c2"]]


def test_collection_tiers_from_json_is_tolerant():
    # Malformed entries dropped; a non-int tier falls back to tier 0; non-list → [].
    data = [
        {"id": "a", "name": "A", "tier": "oops"},
        {"name": "no id"},
        "garbage",
        {"id": "b", "name": "B", "tier": 10},
    ]
    assert collection_tiers_from_json(data) == [["a"], ["b"]]
    assert collection_tiers_from_json("not a list") == []
    assert collection_tiers_from_json([]) == []


def test_resolve_profile_collections_resolves_names_and_keeps_tiers():
    # #280: a profile declares its default collection set by NAME + tier; seeding
    # resolves names → live ids, building the `collections.json` rows.
    spec = make_spec(default_user="u")
    a = _coll(spec, "Fab Docs")
    b = _coll(spec, "Archive")
    rows = resolve_profile_collections(spec, [("Fab Docs", 0), ("Archive", 10)])
    assert rows == [
        {"id": a, "name": "Fab Docs", "tier": 0},
        {"id": b, "name": "Archive", "tier": 10},
    ]


def test_resolve_profile_collections_skips_unresolvable_names(caplog):
    # Q9: a name matching no live collection is skipped + logged, never a hard fail
    # (a stale profile default must not block item creation).
    spec = make_spec(default_user="u")
    a = _coll(spec, "Fab Docs")
    rows = resolve_profile_collections(spec, [("Fab Docs", 0), ("ghost", 0)])
    assert rows == [{"id": a, "name": "Fab Docs", "tier": 0}]
    assert "ghost" in caplog.text


def test_resolve_collection_by_name_is_case_insensitive():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Equipment Log")
    assert resolve_collection(spec, "equipment log") == {
        "status": "ok",
        "id": cid,
        "name": "Equipment Log",
    }


def test_resolve_collection_by_id():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    assert resolve_collection(spec, cid) == {"status": "ok", "id": cid, "name": "Defects"}


def test_resolve_collection_ambiguous_name_returns_candidates():
    spec = make_spec(default_user="u")
    a, b = _coll(spec, "Logs"), _coll(spec, "logs")  # collide under casefold
    got = resolve_collection(spec, "LOGS")
    assert got["status"] == "ambiguous"
    assert {c["id"] for c in got["candidates"]} == {a, b}


def test_resolve_collection_miss_returns_available():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    got = resolve_collection(spec, "nonexistent")
    assert got["status"] == "not_found"
    assert {c["id"] for c in got["available"]} == {cid}


def test_resolve_collection_miss_suggests_the_closest_names_not_the_whole_catalog():
    """A miss used to answer with every collection in the deployment — one typo
    dumped the catalog into the turn. The useful part of that answer was always
    the handful of names near what was asked for."""
    spec = make_spec(default_user="u")
    for i in range(80):
        _coll(spec, f"Unrelated {i}")
    wanted = _coll(spec, "Defect Log")

    got = resolve_collection(spec, "Defect Logs")

    assert got["status"] == "not_found"
    assert len(got["available"]) < 80
    assert wanted in {c["id"] for c in got["available"]}  # the near miss survives the cut
    assert got["total"] == 81  # the agent can still tell how many exist
