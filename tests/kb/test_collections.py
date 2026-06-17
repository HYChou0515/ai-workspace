"""Topic Hub §5 — the collection set is a workspace file (`collections.json`);
`resolve_collection` turns a user-given id-or-name into the canonical {id, name}
the agent records there (resolve only, no write)."""

from __future__ import annotations

from workspace_app.kb.collections import collection_ids_from_json, resolve_collection
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection


def _coll(spec, name: str) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def test_collection_ids_from_json_extracts_ids_in_order():
    data = [{"id": "c1", "name": "Defects"}, {"id": "c2", "name": "Logs"}]
    assert collection_ids_from_json(data) == ["c1", "c2"]


def test_collection_ids_from_json_skips_malformed_entries():
    # a hand-edited file mustn't crash the turn: drop entries with no/blank id.
    data = [{"id": "c1", "name": "A"}, {"name": "no id"}, {"id": "", "name": "blank"}, "garbage"]
    assert collection_ids_from_json(data) == ["c1"]
    assert collection_ids_from_json("not a list") == []


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
