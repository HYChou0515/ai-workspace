"""#307 — the access-scope predicate builder's group plumbing (unit level).

`subjects_of` folds a user's groups into their grant targets, and
`collection_access_scope` degrades gracefully when no `groups_provider` is
injected (the pre-groups default) — the path production never takes but every
resource whose scope hasn't wired groups yet relies on.
"""

from specstar import UNRESTRICTED

from workspace_app.perm.scope import collection_access_scope, subjects_of


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
