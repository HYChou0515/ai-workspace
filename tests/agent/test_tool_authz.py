"""#309 — agent-tool authorization: an item-level tool the AI runs is gated by
`authorize(Actor.ai(ceiling ∩ speaker), verb, item.permission)`. A prompt-injected
model can at worst do what the current speaker may do on the item, never more.
"""

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tool_authz import authorize_tool, ceiling_from_tools
from workspace_app.agent.tools import (
    delete_file_impl,
    edit_file_impl,
    exec_impl,
    exists_impl,
    make_deck_impl,
    read_file_impl,
    read_image_impl,
    write_file_impl,
)
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.agent_config import AgentConfig
from workspace_app.resources.groups import Group


def _spec_with_item(permission: Permission | None, *, owner: str = "bob") -> tuple[object, str]:
    spec = make_spec(default_user=owner)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(owner):
        iid = rm.create(RcaInvestigation(title="t", owner=owner, permission=permission)).resource_id
    return spec, iid


def _ctx(
    spec: object,
    iid: str,
    *,
    acting_user: str,
    agent_config: AgentConfig | None = None,
) -> RunContextWrapper:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id=iid,
            files=WorkspaceFiles(MemoryFileStore()),
            spec=spec,  # ty: ignore[invalid-argument-type]
            app_slug="rca",
            acting_user=acting_user,
            agent_config=agent_config,
        )
    )


async def test_write_file_denied_when_speaker_lacks_edit_content():
    """alice can converse (drive the agent) but not edit_content → the AI she
    drives is refused the write, and nothing lands in the workspace."""
    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], converse=["user:alice"])
    )
    ctx = _ctx(spec, iid, acting_user="alice")
    out = await write_file_impl(ctx, "/a.txt", "hi")
    assert "don't have permission" in out
    assert await exists_impl(ctx, "/a.txt") is False  # the write was blocked


async def test_write_file_allowed_when_speaker_has_edit_content():
    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], edit_content=["user:alice"])
    )
    ctx = _ctx(spec, iid, acting_user="alice")
    assert "wrote" in await write_file_impl(ctx, "/a.txt", "hi")


async def test_public_item_is_unrestricted_zero_regression():
    """A default (public) item enforces nothing — the AI runs its tools for anyone,
    exactly as before #309."""
    spec, iid = _spec_with_item(None)  # no permission ≡ public
    ctx = _ctx(spec, iid, acting_user="carol")
    assert "wrote" in await write_file_impl(ctx, "/a.txt", "hi")


async def test_every_guarded_tool_is_denied_when_the_speaker_lacks_the_verb():
    """alice can only converse — every read_content / edit_content / execute tool
    the AI runs for her is refused (each guard sits at the top of its impl, before
    it touches the sandbox / describer / deck machinery)."""
    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], converse=["user:alice"])
    )
    ctx = _ctx(spec, iid, acting_user="alice")
    results = [
        await read_file_impl(ctx, "/a.txt"),
        await edit_file_impl(ctx, "/a.txt", "x", "y"),
        await delete_file_impl(ctx, "/a.txt"),
        await read_image_impl(ctx, "/a.png"),
        await make_deck_impl(ctx, "a deck"),
        await write_file_impl(ctx, "/a.txt", "hi"),
        await exec_impl(ctx, ["echo", "hi"]),
    ]
    assert all("don't have permission" in r for r in results)


def test_authorize_tool_is_noop_without_an_item_context():
    """A wiki / KB / workflow turn (no spec+item+app) is not item-gated here."""
    ctx = AgentToolContext(files=WorkspaceFiles(MemoryFileStore()))
    assert authorize_tool(ctx, "edit_content") is None


def test_authorize_tool_is_noop_for_an_unknown_app_or_missing_item():
    """Fail-open on the two things that can only happen off the real request path:
    a slug no App registers, and an item id that doesn't resolve (the underlying
    tool then reports the miss on its own)."""
    spec, iid = _spec_with_item(Permission(visibility="private"))
    files = WorkspaceFiles(MemoryFileStore())
    bad_app = AgentToolContext(
        investigation_id=iid,
        files=files,
        spec=spec,  # ty: ignore[invalid-argument-type]
        app_slug="bogus-app",
        acting_user="alice",
    )
    assert authorize_tool(bad_app, "edit_content") is None
    missing = AgentToolContext(
        investigation_id="rca-investigation:missing",
        files=files,
        spec=spec,  # ty: ignore[invalid-argument-type]
        app_slug="rca",
        acting_user="alice",
    )
    assert authorize_tool(missing, "edit_content") is None


def test_ceiling_from_tools_maps_the_allow_list():
    assert ceiling_from_tools(["read_file", "exec"]) == frozenset({"read_content", "execute"})
    assert ceiling_from_tools(["mention_user"]) == frozenset()  # not an item-verb tool
    assert "edit_content" in ceiling_from_tools(None)  # None ⇒ the full workspace toolset


def test_hard_barred_verbs_can_never_enter_a_tool_ceiling():
    """No tool maps to `use_terminal` / `change_permission`, so the AI can never
    acquire them through a preset (and `authorize` hard-bars them regardless) —
    the #309 guarantee that a prompt-injection can't rewire access or open a
    shell."""
    from workspace_app.agent.tool_authz import TOOL_VERBS

    every = ceiling_from_tools(list(TOOL_VERBS))
    assert "use_terminal" not in every
    assert "change_permission" not in every


# ── the AI actor was the one actor built without the speaker's groups ──


def _group_granted_item(verb_grants: dict[str, list[str]]) -> tuple[object, str, str]:
    """An item whose grants may name a group alice belongs to. Returns
    ``(spec, item id, group id)`` — the group id is only knowable after the
    group exists, so the permission is written second."""
    spec = make_spec(default_user="bob")
    grm = spec.get_resource_manager(Group)
    with grm.using("bob"):
        gid = grm.create(Group(name="ops", members=["alice"])).resource_id
    rm = spec.get_resource_manager(RcaInvestigation)
    grants = {k: [g.format(gid=gid) for g in v] for k, v in verb_grants.items()}
    with rm.using("bob"):
        iid = rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(visibility="restricted", **grants),
            )
        ).resource_id
    return spec, iid, gid


def test_a_group_grant_reaches_the_ai_the_way_it_reaches_the_person():
    """`Actor.ai` was the ONLY actor in the codebase built with no groups, while
    every human path passes `groups_of(spec, user)`. So a verb granted to
    `group:<id>` was reachable by the person and refused to the agent they were
    driving — which reads, from a chat, as "I am an admin and my agent says I
    have no permission", with the tools granted directly still working."""
    spec, iid, _ = _group_granted_item(
        {
            "read_meta": ["user:alice"],
            "read_content": ["user:alice"],
            "execute": ["group:{gid}"],
        }
    )
    ctx = _ctx(spec, iid, acting_user="alice").context
    assert authorize_tool(ctx, "read_content") is None  # granted directly — always worked
    assert authorize_tool(ctx, "execute") is None  # granted through the group


def test_the_speakers_groups_are_read_once_for_the_whole_turn(monkeypatch):
    """One indexed query per TURN, not per tool call. `authorize_tool` already
    costs two point reads per call and sits on the request path; a third query
    on every file the agent touches is the shape that pinned the loop before."""
    from workspace_app.resources import groups as groups_module

    seen: list[str] = []
    real = groups_module.groups_of

    def counting(spec: object, user: str) -> frozenset[str]:
        seen.append(user)
        return real(spec, user)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(groups_module, "groups_of", counting)
    spec, iid, _ = _group_granted_item({"read_meta": ["user:alice"], "execute": ["group:{gid}"]})
    ctx = _ctx(spec, iid, acting_user="alice").context
    authorize_tool(ctx, "execute")
    authorize_tool(ctx, "execute")
    authorize_tool(ctx, "read_content")
    assert seen == ["alice"]


# ── one sentence was standing in for two unrelated causes ──


def test_a_tool_switched_off_is_not_reported_as_a_permission_problem():
    """The item is PUBLIC — nobody's permissions are involved. The agent simply
    has no `exec` in its resolved tool set, and saying "you don't have permission"
    sends the reader to the permission panel, where there is nothing to find and
    nothing they could change would help."""
    spec, iid = _spec_with_item(None)
    ctx = _ctx(
        spec,
        iid,
        acting_user="alice",
        agent_config=AgentConfig(name="x", allowed_tools=["read_file", "list_files"]),
    ).context
    assert authorize_tool(ctx, "read_content") is None  # the tools it does hold still work
    denied = authorize_tool(ctx, "execute")
    assert denied is not None
    assert "don't have permission" not in denied
    assert "tool settings" in denied


def test_a_permission_refusal_still_names_permission():
    """The control for the test above: when the refusal really IS the person's
    grants, the sentence must not drift into blaming the tool set."""
    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], converse=["user:alice"])
    )
    ctx = _ctx(spec, iid, acting_user="alice").context
    denied = authorize_tool(ctx, "execute")
    assert denied is not None
    assert "don't have permission" in denied
    assert "tool settings" not in denied


def test_a_permission_refusal_leaves_a_log_line_naming_who_and_what(caplog):
    """The ceiling refusal has logged a WARNING since #309; the grant refusal
    logged NOTHING, so the more common of the two causes was invisible in a
    deployment — which is why it could only be guessed at from a chat bubble."""
    import logging

    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], converse=["user:alice"])
    )
    ctx = _ctx(spec, iid, acting_user="alice").context
    with caplog.at_level(logging.WARNING, logger="workspace_app.agent.tool_authz"):
        assert authorize_tool(ctx, "execute") is not None
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "execute" in said
    assert "alice" in said
    assert iid in said
    assert "restricted" in said


def test_a_legacy_tool_name_still_carries_its_verb():
    """`build_tools` renames legacy entries before it registers them, so a config
    naming `ls` gets a working `list_files`. The ceiling read the SAME list
    without renaming, so that tool was registered and then refused every call —
    the #537 shape (a tool that can only say no reads as "stop trying")."""
    assert ceiling_from_tools(["ls"]) == frozenset({"read_content"})
