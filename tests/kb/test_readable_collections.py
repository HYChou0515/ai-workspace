"""#305 — `readable_collection_ids`: the subset of collection ids a user may
`read_content`, the transitive gate the KB sub-agent's scope is filtered through.
"""

from specstar import SpecStar

from workspace_app.kb.collections import readable_collection_ids
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.groups import Group
from workspace_app.resources.kb import Collection


def _new_collection(
    spec: SpecStar, *, by: str, permission: Permission | None = None, name: str = "c"
) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(by):
        return rm.create(Collection(name=name, permission=permission)).resource_id


def test_filters_to_what_the_user_can_read_content():
    spec = make_spec()
    public = _new_collection(spec, by="bob")  # no permission ≡ public
    private = _new_collection(spec, by="bob", permission=Permission(visibility="private"))
    granted = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_content=["user:alice"])
    )
    ungranted = _new_collection(spec, by="bob", permission=Permission(visibility="restricted"))
    ids = [public, private, granted, ungranted]

    # alice sees the public one + the one she's granted read_content; input order kept
    assert readable_collection_ids(spec, ids, "alice") == [public, granted]
    # the owner reads all of their own collections
    assert readable_collection_ids(spec, ids, "bob") == ids


def test_read_meta_grant_is_not_enough_for_read_content():
    """The verbs are orthogonal — being able to SEE a collection (read_meta) does
    not let the sub-agent search its CONTENT (read_content)."""
    spec = make_spec()
    cid = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_meta=["user:alice"])
    )
    assert readable_collection_ids(spec, [cid], "alice") == []


def test_superuser_reads_all():
    spec = make_spec(superusers=frozenset({"root"}))
    private = _new_collection(spec, by="bob", permission=Permission(visibility="private"))
    assert readable_collection_ids(spec, [private], "root", superusers=frozenset({"root"})) == [
        private
    ]


def test_unknown_id_is_dropped():
    spec = make_spec()
    assert readable_collection_ids(spec, ["ghost"], "alice") == []


def _group_with(spec: SpecStar, member: str, *, by: str = "bob") -> str:
    rm = spec.get_resource_manager(Group)
    with rm.using(by):
        return rm.create(Group(name="ops", members=[member])).resource_id


def test_a_group_grant_is_readable_by_a_member():
    """This gate is what a KB sub-agent's scope is filtered through, and it was
    built with `Actor.human(user)` — no groups — while the HTTP route that LISTS
    collections resolves them. So a collection shared to a group appeared in the
    person's list and then could not be searched by the agent they were driving:
    the same "I can see it, my agent says no" shape as the item-tool funnel."""
    spec = make_spec()
    gid = _group_with(spec, "alice")
    cid = _new_collection(
        spec,
        by="bob",
        permission=Permission(visibility="restricted", read_content=[f"group:{gid}"]),
    )
    assert readable_collection_ids(spec, [cid], "alice") == [cid]


def test_a_group_grant_does_not_reach_a_non_member():
    """The control: the group has to be the reason, not the mere presence of a
    group grant."""
    spec = make_spec()
    gid = _group_with(spec, "alice")
    cid = _new_collection(
        spec,
        by="bob",
        permission=Permission(visibility="restricted", read_content=[f"group:{gid}"]),
    )
    assert readable_collection_ids(spec, [cid], "carol") == []
