"""#309 — agent-tool authorization: an item-level tool the AI runs is gated by
`authorize(Actor.ai(ceiling ∩ speaker), verb, item.permission)`. A prompt-injected
model can at worst do what the current speaker may do on the item, never more.
"""

import dataclasses

import pytest
from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tool_authz import TOOL_VERBS, authorize_tool, ceiling_from_tools
from workspace_app.agent.tools import (
    delete_file_impl,
    edit_file_impl,
    exec_impl,
    exists_impl,
    list_files_impl,
    make_deck_impl,
    read_file_impl,
    read_image_impl,
    save_subagent_impl,
    show_file_impl,
    write_file_impl,
)
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.perm.model import Verb
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
    # Asked of the STORE, not of `exists` — that tool is gated too now, so it
    # would answer with a refusal rather than with the truth about the file, and
    # this assertion previously passed only because it was NOT gated.
    assert await ctx.context.files.exists(iid, "/a.txt") is False


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


#: One call per name in `TOOL_VERBS`. Keyed by name — and the test below asserts
#: the keys ARE `TOOL_VERBS` — so a tool that declares a verb cannot be added
#: without a case here proving it actually checks it. `list_files` and `exists`
#: were declared here and gated nowhere; the old version of this test listed the
#: seven impls somebody remembered, which is why nothing noticed.
_CALLS = {
    "read_file": lambda c: read_file_impl(c, "/a.txt"),
    "read_image": lambda c: read_image_impl(c, "/a.png"),
    "show_file": lambda c: show_file_impl(c, "/a.txt"),
    "list_files": lambda c: list_files_impl(c),
    "exists": lambda c: exists_impl(c, "/a.txt"),
    "write_file": lambda c: write_file_impl(c, "/a.txt", "hi"),
    "edit_file": lambda c: edit_file_impl(c, "/a.txt", "x", "y"),
    "delete_file": lambda c: delete_file_impl(c, "/a.txt"),
    "exec": lambda c: exec_impl(c, ["echo", "hi"]),
    "make_deck": lambda c: make_deck_impl(c, "a deck"),
    "save_subagent": lambda c: save_subagent_impl(c, "digger", "Digs logs", [], "You dig."),
}


async def test_every_tool_that_declares_a_verb_actually_checks_it():
    """alice can only converse — every tool `TOOL_VERBS` claims is item-gated is
    refused, with the guard at the top of its impl, before it touches the
    workspace / sandbox / describer / deck machinery.

    Driven from `TOOL_VERBS` rather than from a hand-kept list: `list_files` and
    `exists` were IN that table and gated nowhere, so they answered for everyone,
    always, regardless of any grant. The table is the funnel's scope — it is not
    yet every tool that touches an item, and the module docstring now says which
    ones are still outside — but nothing may sit in it without a case here."""
    assert set(_CALLS) == set(TOOL_VERBS)
    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], converse=["user:alice"])
    )
    ctx = _ctx(spec, iid, acting_user="alice")
    for name, call in _CALLS.items():
        assert "don't have permission" in str(await call(ctx)), name


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


def _one_warning(caplog, ctx, verb: Verb) -> str:
    import logging

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="workspace_app.agent.tool_authz"):
        assert authorize_tool(ctx, verb) is not None
    lines = [r.getMessage() for r in caplog.records if r.name == "workspace_app.agent.tool_authz"]
    assert len(lines) == 1, lines  # one event, one line — the model may retry hard
    return lines[0]


def test_a_permission_refusal_leaves_a_log_line_naming_who_and_what(caplog):
    """The ceiling refusal has logged a WARNING since #309; the grant refusal
    logged NOTHING, so the more common of the two causes was invisible in a
    deployment — which is why it could only be guessed at from a chat bubble."""
    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], converse=["user:alice"])
    )
    ctx = _ctx(spec, iid, acting_user="alice").context
    said = _one_warning(caplog, ctx, "execute")
    assert "execute" in said
    assert "alice" in said
    assert iid in said
    assert "visibility=restricted" in said
    assert "owner=bob" in said


def test_the_log_says_which_of_the_two_refusals_it_was(caplog):
    """The two causes need different moves from whoever reads them, and they are
    told apart by measurement, not by advice: `verb in ceiling` is what the code
    actually evaluated. It is NOT said to the model — the ceiling branch
    short-circuits before any grant is looked at, so it cannot claim the grants
    would have allowed it."""
    # ceiling: a PUBLIC item, so nobody's grants are in play at all.
    spec, iid = _spec_with_item(None)
    ungranted = _ctx(
        spec,
        iid,
        acting_user="alice",
        agent_config=AgentConfig(name="x", allowed_tools=["read_file", "list_files"]),
    ).context
    assert authorize_tool(ungranted, "read_content") is None  # what it does hold still works
    assert "in ai ceiling=False" in _one_warning(caplog, ungranted, "execute")

    # grant: the tool is held, the person is not allowed.
    spec, iid = _spec_with_item(
        Permission(visibility="restricted", read_meta=["user:alice"], converse=["user:alice"])
    )
    assert "in ai ceiling=True" in _one_warning(
        caplog, _ctx(spec, iid, acting_user="alice").context, "execute"
    )


def test_the_refusal_does_not_write_the_items_grant_lists_to_the_log(caplog):
    """The grant lists are a roster of user ids — under SSO, an address list —
    and this line fires on a path any speaker can trigger as often as the model
    retries. It names the item so somebody can go and look; it does not copy
    out who else can reach it."""
    spec, iid = _spec_with_item(
        Permission(
            visibility="restricted",
            read_meta=["user:alice"],
            converse=["user:alice"],
            execute=["user:carol@example.com", "group:payroll"],
        )
    )
    said = _one_warning(caplog, _ctx(spec, iid, acting_user="alice").context, "execute")
    assert "carol@example.com" not in said
    assert "payroll" not in said


def test_a_legacy_tool_name_still_carries_its_verb():
    """`build_tools` renames legacy entries before it registers them, so a config
    naming `ls` gets a working `list_files`. The ceiling read the SAME list
    without renaming, so that tool was registered and then refused every call —
    the #537 shape (a tool that can only say no reads as "stop trying")."""
    assert ceiling_from_tools(["ls"]) == frozenset({"read_content"})


def test_a_ceiling_refusal_does_not_claim_the_speaker_is_in_no_groups(caplog):
    """The count is read off the actor, and on a CEILING refusal that actor is
    the throwaway built without groups. Printing `0` there tells whoever is
    diagnosing "this user is in no groups" — the one wrong conclusion for the
    symptom the line exists to explain. Nothing is asked for, so nothing is
    claimed."""
    spec, iid = _spec_with_item(None)
    ctx = _ctx(
        spec,
        iid,
        acting_user="alice",
        agent_config=AgentConfig(name="x", allowed_tools=["read_file"]),
    ).context
    said = _one_warning(caplog, ctx, "execute")
    assert "speaker groups=n/a" in said
    assert "visibility=public" in said  # a public item DOES reach a refusal, via the ceiling


def test_a_grant_refusal_reports_the_groups_it_actually_looked_at(caplog):
    """The control for the case above: where groups WERE resolved, the count is
    the real one — so the two cases cannot be told apart by luck."""
    spec, iid, _ = _group_granted_item({"read_meta": ["user:alice"]})
    said = _one_warning(caplog, _ctx(spec, iid, acting_user="alice").context, "execute")
    assert "speaker groups=1" in said


def test_a_turn_with_no_speaker_asks_for_no_groups(monkeypatch):
    """`acting_user` is "" on paths with nobody behind them. Before this the AI
    carried no groups at all, so nothing asked; now something does, and an empty
    string into a `members.contains(...)` query is element membership today and
    a substring `LIKE` the moment `Group.members` loses its list registration —
    at which point "" is a substring of every member."""
    from workspace_app.resources import groups as groups_module

    monkeypatch.setattr(
        groups_module,
        "groups_of",
        lambda spec, user: pytest.fail(f"asked for the groups of {user!r}"),
    )
    spec, iid = _spec_with_item(Permission(visibility="private"))
    ctx = _ctx(spec, iid, acting_user="").context
    assert authorize_tool(ctx, "read_content") is not None


def test_a_derived_context_does_not_inherit_the_memo():
    """`_speaker_groups` belongs to `acting_user`. `dataclasses.replace` —
    which `subagent_run` and `compaction` both use — must rebuild it rather than
    carry one speaker's memberships onto a context built for another."""
    spec, iid, _ = _group_granted_item({"read_meta": ["user:alice"], "execute": ["group:{gid}"]})
    ctx = _ctx(spec, iid, acting_user="alice").context
    assert authorize_tool(ctx, "execute") is None
    assert ctx._speaker_groups == frozenset({g for g in ctx._speaker_groups})  # resolved
    assert ctx._speaker_groups  # non-empty, so inheriting it would be visible

    child = dataclasses.replace(ctx, history=[])
    assert child._speaker_groups is None
