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


def _spec_with_item(permission: Permission | None, *, owner: str = "bob") -> tuple[object, str]:
    spec = make_spec(default_user=owner)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(owner):
        iid = rm.create(RcaInvestigation(title="t", owner=owner, permission=permission)).resource_id
    return spec, iid


def _ctx(spec: object, iid: str, *, acting_user: str) -> RunContextWrapper:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id=iid,
            files=WorkspaceFiles(MemoryFileStore()),
            spec=spec,  # ty: ignore[invalid-argument-type]
            app_slug="rca",
            acting_user=acting_user,
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
