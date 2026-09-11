"""#608 P1 — the Group model gains a single (optional) `owner` and a `maintainers`
list. `effective_owner` resolves the group's authority: the explicit `owner`, or
— when unset (every group created before #608, and the default) — the record's
`created_by`. Membership stays the payoff (`groups_of`); this only adds WHO may
manage the group.
"""

from workspace_app.resources import make_spec
from workspace_app.resources.groups import Group, effective_owner, groups_of


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


def test_an_empty_user_is_never_sent_into_the_membership_query():
    """The one caller with no speaker (a turn nobody is behind) must not reach
    the store. `.contains` is element membership today and degrades to a
    substring `LIKE` on a SQL backend the moment `members` loses its list
    registration — and "" is a substring of every member, so that caller would
    collect every group there is."""
    spec = make_spec()
    rm = spec.get_resource_manager(Group)
    with rm.using("bob"):
        rm.create(Group(name="ops", members=["alice"]))

    def _boom(*a, **k):
        raise AssertionError("the store was queried for an empty user")

    rm.list_resources = _boom  # ty: ignore[invalid-assignment]
    spec.get_resource_manager = lambda *a, **k: rm  # ty: ignore[invalid-assignment]
    assert groups_of(spec, "") == frozenset()
