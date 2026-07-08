"""#307 — the access-scope predicate builder's group plumbing (unit level).

`subjects_of` folds a user's groups into their grant targets, and
`collection_access_scope` degrades gracefully when no `groups_provider` is
injected (the pre-groups default) — the path production never takes but every
resource whose scope hasn't wired groups yet relies on.

#494 — the "absent visibility ≡ public" clause MUST be `isna()` (absent-OR-null),
never `is_null()` (present-AND-null): a row written before the visibility field
was indexed has NO cell for it, and on postgres/sqlite `is_null()` does not match
an absent cell, so a legacy doc/collection would be HIDDEN from every non-owner
(the "listed + viewable but 404 on open" bug). This can't be caught behaviourally
on the in-memory / disk test backends — they evaluate conditions in Python and
treat a missing key as null, so `is_null()` and `isna()` behave identically there
(which is exactly how the regression shipped green). So the guard below is
STRUCTURAL: it inspects the built predicate and asserts the visibility field is
matched with `isna`, not `is_null`.
"""

from specstar import UNRESTRICTED
from specstar.query_types import DataSearchOperator

from workspace_app.perm.scope import (
    collection_access_scope,
    kbchat_access_scope,
    source_doc_access_scope,
    subjects_of,
)


def _field_operators(scope, user: str = "bob") -> list[tuple[str | None, object]]:
    """Every ``(field, operator)`` leaf in the predicate a scope builds for
    ``user`` — walks the ``DataSearchGroup`` tree the ConditionBuilder emits so a
    test can assert which operator gates a given field."""
    query = scope(user).build()
    out: list[tuple[str | None, object]] = []

    def walk(node: object) -> None:
        conditions = getattr(node, "conditions", None)
        if conditions is not None:
            for child in conditions:
                walk(child)
            return
        out.append((getattr(node, "field_path", None), getattr(node, "operator", None)))

    for condition in query.conditions or []:
        walk(condition)
    return out


def _ops_for(pairs: list[tuple[str | None, object]], field: str) -> set[object]:
    return {op for f, op in pairs if f == field}


def test_subjects_of_folds_in_groups():
    assert subjects_of("alice", ["eng", "sre"]) == [
        "user:alice",
        "group:eng",
        "group:sre",
        "all",
    ]


def test_subjects_of_without_groups_is_just_user_and_all():
    assert subjects_of("alice") == ["user:alice", "all"]


def test_scope_is_unrestricted_for_a_superuser():
    scope = collection_access_scope(frozenset({"root"}))
    assert scope("root") is UNRESTRICTED


def test_scope_without_a_groups_provider_builds_a_predicate():
    # No provider ⇒ no groups folded in (the pre-groups default). A non-superuser
    # still gets a real predicate, not UNRESTRICTED.
    scope = collection_access_scope()
    assert scope("alice") is not UNRESTRICTED


def test_collection_scope_admits_an_absent_visibility_via_isna_not_is_null():
    # #494: a legacy collection with no `permission.visibility` cell must read as
    # public. `isna()` matches an absent cell on every backend; `is_null()` does
    # not (postgres/sqlite) — reverting to it re-hides legacy rows.
    pairs = _field_operators(collection_access_scope())
    ops = _ops_for(pairs, "permission.visibility")
    assert DataSearchOperator.isna in ops
    assert DataSearchOperator.is_null not in ops


def test_source_doc_scope_admits_absent_collection_and_override_via_isna():
    # #494 the reported bug: a pre-#303/#308 SourceDoc has neither
    # `collection_visibility` (the #303 inherited mirror) nor `permission.visibility`
    # (the #308 override) cell. BOTH halves must admit an absent cell as public via
    # `isna()`, or the doc 404s on open in a public collection for every non-owner.
    pairs = _field_operators(source_doc_access_scope())
    for field in ("collection_visibility", "permission.visibility"):
        ops = _ops_for(pairs, field)
        assert DataSearchOperator.isna in ops, field
        assert DataSearchOperator.is_null not in ops, field


def test_kbchat_scope_legacy_shared_with_fallback_uses_isna():
    # #494 same footgun: a pre-#304 chat with an absent `permission.visibility`
    # must still honour its legacy `shared_with` grants — which only fires under
    # `isna()` (an absent cell), never `is_null()`.
    pairs = _field_operators(kbchat_access_scope())
    ops = _ops_for(pairs, "permission.visibility")
    assert DataSearchOperator.isna in ops
    assert DataSearchOperator.is_null not in ops
