"""#608 P1 — the Group model gains a single (optional) `owner` and a `maintainers`
list. `effective_owner` resolves the group's authority: the explicit `owner`, or
— when unset (every group created before #608, and the default) — the record's
`created_by`. Membership stays the payoff (`groups_of`); this only adds WHO may
manage the group.
"""

from workspace_app.resources.groups import Group, effective_owner


def test_effective_owner_falls_back_to_created_by_when_unset():
    # An owner-less group (the default, and every pre-#608 row) is owned by its
    # creator — so existing groups need no migration.
    assert effective_owner(Group(name="g"), created_by="alice") == "alice"


def test_effective_owner_prefers_an_explicit_owner():
    # A superuser can create a group FOR someone else (or ownership is transferred);
    # the explicit owner wins over the record creator.
    assert effective_owner(Group(name="g", owner="dave"), created_by="root") == "dave"


def test_a_group_carries_a_maintainers_list_defaulting_empty():
    g = Group(name="g")
    assert g.maintainers == []
    assert Group(name="g", maintainers=["dave", "erin"]).maintainers == ["dave", "erin"]
