"""#307 — the write checker resolves the acting user's groups (unit level).

The end-to-end "a group write_meta grant lets a member edit" is covered by
tests/api/test_groups.py; here we pin the actor-construction seam directly,
including the no-provider default (the path production never takes — the resolver
is always injected — but a checker built without one must still be safe).
"""

from workspace_app.perm.checker import CollectionPermissionChecker


def test_checker_without_a_groups_provider_resolves_no_groups():
    actor = CollectionPermissionChecker()._actor("alice")
    assert actor.user_id == "alice"
    assert actor.groups == frozenset()


def test_checker_folds_in_the_providers_groups():
    checker = CollectionPermissionChecker(groups_provider=lambda u: frozenset({"eng", "sre"}))
    assert checker._actor("alice").groups == frozenset({"eng", "sre"})
