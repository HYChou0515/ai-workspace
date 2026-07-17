"""Global collections + the shared scope resolver.

A collection flagged ``is_global`` is part of the AI's baseline retrieval scope
in every conversation. The three scope modes (grill D2) compose as:

    unspecified  → global only
    specified S  → S ∪ global
    exclude E    → global \\ E   (and (S ∪ global) \\ E in general)

``resolve_effective_scope`` is the ONE place that union/exclude lives; the
per-user permission filter (readable/discoverable) is applied by the caller ON
TOP of what this returns.
"""

from specstar import SpecStar

from workspace_app.kb.collections import global_collection_ids, resolve_effective_scope
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection


def _coll(spec: SpecStar, *, name: str, is_global: bool = False) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, is_global=is_global))
        .resource_id
    )


def test_global_collection_ids_returns_only_the_flagged_ones():
    spec = make_spec()
    g1 = _coll(spec, name="Sales-KB", is_global=True)
    g2 = _coll(spec, name="HR-policies", is_global=True)
    _coll(spec, name="my-project", is_global=False)
    assert set(global_collection_ids(spec)) == {g1, g2}


def test_unspecified_scope_is_the_global_set():
    spec = make_spec()
    g = _coll(spec, name="g", is_global=True)
    _coll(spec, name="other", is_global=False)
    assert resolve_effective_scope(spec, None) == [g]
    assert resolve_effective_scope(spec, []) == [g]  # empty ≡ unspecified


def test_specified_is_unioned_with_global():
    spec = make_spec()
    g = _coll(spec, name="g", is_global=True)
    c = _coll(spec, name="c", is_global=False)
    # specified first (input order), then globals not already present
    assert resolve_effective_scope(spec, [c]) == [c, g]


def test_a_specified_global_is_not_duplicated():
    spec = make_spec()
    g = _coll(spec, name="g", is_global=True)
    assert resolve_effective_scope(spec, [g]) == [g]


def test_exclude_removes_a_global():
    spec = make_spec()
    g1 = _coll(spec, name="g1", is_global=True)
    g2 = _coll(spec, name="g2", is_global=True)
    assert resolve_effective_scope(spec, None, excluded=[g1]) == [g2]


def test_exclude_applies_after_the_union():
    spec = make_spec()
    g1 = _coll(spec, name="g1", is_global=True)
    g2 = _coll(spec, name="g2", is_global=True)
    c = _coll(spec, name="c", is_global=False)
    # (c ∪ {g1,g2}) \ {g1} → c, g2
    assert resolve_effective_scope(spec, [c], excluded=[g1]) == [c, g2]


def test_no_globals_and_unspecified_is_empty_hard_cutover():
    # grill D5: hard cutover — unspecified with NO global collections searches
    # nothing (it is the operator's job to designate globals first).
    spec = make_spec()
    _coll(spec, name="c", is_global=False)
    assert resolve_effective_scope(spec, None) == []


def test_excluding_a_specified_collection_removes_it_too():
    # exclusion wins over specification for the same id (explicit removal).
    spec = make_spec()
    c = _coll(spec, name="c", is_global=False)
    assert resolve_effective_scope(spec, [c], excluded=[c]) == []
