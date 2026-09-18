"""`install_skill(entry_id)` — a skill hub entry becomes a copy in THIS
workspace (plan P5).

Same copy shape as a materialized package skill (`.skill/<name>/` + `.origin`),
so everything that already works on a copy — the index, `read_skill`, the
Refresh button, "has an update" — works on it with no new code path. What is
new is the refusals: an entry the installer cannot see is GONE (Q10 — no
"exists but private" leak), and a folder that already holds that name is never
overwritten (plan install step 4).
"""

from __future__ import annotations

import msgspec
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import install_skill_impl, read_skill_impl
from workspace_app.apps.skill_hub import (
    SkillHubEntry,
    SkillHubReview,
    SkillHubStore,
    register_skill_hub,
)
from workspace_app.apps.skill_payload import ORIGIN_FILE, SkillOrigin, origin_for
from workspace_app.apps.skills import WORKSPACE_SKILL_DIR, workspace_skill_metas
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec

PAYLOAD = {
    "SKILL.md": b"---\nname: triage-reflow\ndescription: Triage reflow defects.\n---\n\n# How\n",
    "references/glossary.md": b"reflow: solder step\n",
}


def _hub():
    spec = make_spec(default_user="system")
    register_skill_hub(spec)
    return spec, SkillHubStore(spec, MemoryFileStore())


async def _alices(hub: SkillHubStore, *, tools: list[str] | None = None) -> str:
    return await hub.publish(
        owner="alice",
        name="triage-reflow",
        description="Triage reflow defects.",
        source_item="inv-alice",
        source_app="pm",
        source_profile="default",
        payload=PAYLOAD,
        referenced_tools=tools or [],
        review=SkillHubReview(verdict="ok"),
    )


def _ctx(hub: SkillHubStore | None, *, user: str = "bob") -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-bob",
            files=WorkspaceFiles(MemoryFileStore()),
            app_slug="rca",
            template_profile="default",
            acting_user=user,
            skill_hub=hub,
        )
    )


async def test_an_installed_entry_is_a_copy_the_next_turn_can_load():
    """The real entry point for "the index has it": the same reader the turn's
    prompt index and `read_skill` use, on the same workspace."""
    _spec, hub = _hub()
    entry = await _alices(hub)
    ctx = _ctx(hub)

    out = await install_skill_impl(ctx, entry)

    assert "error" not in out and "triage-reflow" in out
    files, inv = ctx.context.files, ctx.context.investigation_id
    assert files is not None and inv is not None
    assert [m.name for m in await workspace_skill_metas(files, inv)] == ["triage-reflow"]
    assert (await read_skill_impl(ctx, "triage-reflow")).strip() == "# How"
    assert await files.read(
        inv, f"/{WORKSPACE_SKILL_DIR}/triage-reflow/references/glossary.md"
    ) == (b"reflow: solder step\n")
    origin = msgspec.json.decode(
        await files.read(inv, f"/{WORKSPACE_SKILL_DIR}/triage-reflow/{ORIGIN_FILE}"),
        type=SkillOrigin,
    )
    assert origin == origin_for("hub", PAYLOAD, entry=entry)


async def test_tools_this_app_lacks_are_named_not_hidden():
    """Plan install step 2: told, not blocked. `query_entity` is registered but
    outside `rca`'s ceiling; `exec` is inside it."""
    _spec, hub = _hub()
    entry = await _alices(hub, tools=["exec", "query_entity"])

    out = await install_skill_impl(_ctx(hub), entry)

    assert "error" not in out
    assert "query_entity" in out and "does not have" in out
    assert "pm" in out, "and which App it was written in"


async def test_a_folder_with_that_name_is_never_overwritten():
    _spec, hub = _hub()
    entry = await _alices(hub)
    ctx = _ctx(hub)
    files, inv = ctx.context.files, ctx.context.investigation_id
    assert files is not None and inv is not None
    mine = b"---\nname: triage-reflow\ndescription: mine\n---\n\nmy own\n"
    await files.write(inv, f"/{WORKSPACE_SKILL_DIR}/triage-reflow/SKILL.md", mine)

    out = await install_skill_impl(ctx, entry)

    assert out.startswith("error:") and "triage-reflow" in out
    assert await files.read(inv, f"/{WORKSPACE_SKILL_DIR}/triage-reflow/SKILL.md") == mine
    assert not await files.exists(inv, f"/{WORKSPACE_SKILL_DIR}/triage-reflow/{ORIGIN_FILE}")


async def test_the_refusal_names_whose_copy_is_in_the_way():
    """「你已經有 alice 的 triage-reflow」— when the folder in the way is itself a
    hub copy, say whose, so the person knows it is the same skill, not a clash."""
    spec, hub = _hub()
    alices = await _alices(hub)
    ctx = _ctx(hub)
    assert "error" not in await install_skill_impl(ctx, alices)
    carols = await hub.publish(
        owner="carol",
        name="triage-reflow",
        description="d",
        source_item="inv-carol",
        source_app="rca",
        source_profile="default",
        payload=PAYLOAD,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )

    out = await install_skill_impl(ctx, carols)

    assert out.startswith("error:") and "alice" in out


async def test_an_entry_the_installer_cannot_see_is_gone_the_same_as_one_that_never_was():
    """Q10. Three cases, one answer, one wording — so a refusal never says
    "this exists but not for you"."""
    spec, hub = _hub()
    private = await _alices(hub)
    rm = spec.get_resource_manager(SkillHubEntry)
    rm.update(
        private,
        msgspec.structs.replace(rm.get(private).data, permission=Permission(visibility="private")),
    )
    deleted = await hub.publish(
        owner="carol",
        name="gone",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="default",
        payload={"SKILL.md": b"---\nname: gone\ndescription: d\n---\n\nx"},
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )
    rm.delete(deleted)

    ctx = _ctx(hub)
    answers = [await install_skill_impl(ctx, e) for e in (private, deleted, "never-was")]

    assert all(a.startswith("error:") for a in answers)
    assert (
        len(
            {
                a.replace(private, "X").replace(deleted, "X").replace("never-was", "X")
                for a in answers
            }
        )
        == 1
    )
    files = ctx.context.files
    assert files is not None and await files.ls("inv-bob", f"/{WORKSPACE_SKILL_DIR}/") == []


async def test_the_owner_can_install_her_own_private_entry():
    spec, hub = _hub()
    entry = await _alices(hub)
    rm = spec.get_resource_manager(SkillHubEntry)
    rm.update(
        entry,
        msgspec.structs.replace(rm.get(entry).data, permission=Permission(visibility="private")),
    )

    out = await install_skill_impl(_ctx(hub, user="alice"), entry)

    assert "error" not in out


async def test_without_the_hub_the_tool_says_where_it_works():
    out = await install_skill_impl(_ctx(None), "any")
    assert out.startswith("error:") and "App workspace turn" in out
