"""`partition_collection_disclosure` + `resolve_withheld` — the collection-layer
glue that feeds the permission-disclosure probe (readable searched, discoverable
disclosed, hidden dropped) and turns disclosed ids into the id+name+owner records
the FE renders.
"""

from workspace_app.kb.collections import (
    partition_collection_disclosure,
    readable_collection_ids,
    resolve_withheld,
)
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, WithheldSource


def _coll(spec, *, by, permission=None, name="c") -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(by):
        return rm.create(Collection(name=name, permission=permission)).resource_id


def test_partition_splits_readable_discoverable_hidden():
    spec = make_spec()
    public = _coll(spec, by="bob")  # no permission ≡ public → readable
    granted = _coll(
        spec, by="bob", permission=Permission(visibility="restricted", read_content=["user:alice"])
    )
    disc = _coll(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=["user:alice"])
    )
    private = _coll(spec, by="bob", permission=Permission(visibility="private"))
    ids = [public, granted, disc, private]

    part = partition_collection_disclosure(spec, ids, "alice")
    assert part.readable == [public, granted]
    assert part.discoverable == [disc]  # read_meta only → disclosed, not searched
    assert part.hidden == [private]  # no read_meta → stays a 404, never disclosed


def test_readable_tier_is_identical_to_readable_collection_ids():
    """Swapping a caller onto the partition must not change the searched scope."""
    spec = make_spec()
    ids = [
        _coll(spec, by="bob"),
        _coll(spec, by="bob", permission=Permission(visibility="private")),
        _coll(
            spec,
            by="bob",
            permission=Permission(visibility="restricted", read_content=["user:alice"]),
        ),
        _coll(spec, by="bob", permission=Permission(visibility="restricted")),
    ]
    part = partition_collection_disclosure(spec, ids, "alice")
    assert part.readable == readable_collection_ids(spec, ids, "alice")


def test_superuser_reads_everything_nothing_discoverable():
    spec = make_spec(superusers=frozenset({"root"}))
    private = _coll(spec, by="bob", permission=Permission(visibility="private"))
    part = partition_collection_disclosure(spec, [private], "root", superusers=frozenset({"root"}))
    assert part.readable == [private]
    assert part.discoverable == []


def test_unknown_id_is_dropped_from_every_tier():
    spec = make_spec()
    part = partition_collection_disclosure(spec, ["ghost"], "alice")
    assert part.readable == part.discoverable == part.hidden == []


def test_resolve_withheld_maps_ids_to_name_and_owner():
    spec = make_spec()
    cid = _coll(spec, by="bob", name="Sales-2026")
    assert resolve_withheld(spec, [cid]) == [
        WithheldSource(collection_id=cid, name="Sales-2026", owner="bob")
    ]


def test_resolve_withheld_dedupes_and_skips_deleted():
    spec = make_spec()
    cid = _coll(spec, by="bob", name="R&D")
    # a repeated id (two sub-agents disclosed it) chips once; an unknown id is skipped
    out = resolve_withheld(spec, [cid, cid, "ghost"])
    assert out == [WithheldSource(collection_id=cid, name="R&D", owner="bob")]
