"""`search_skill_hub(query)` — the agent finds a skill for the user (plan P6).

What comes back is a list the user can pick from: `owner/name`, the
description, the entry id `install_skill` takes, which App it was written in,
and — computed against THIS item's App — the tools it mentions that this App
does not have. Roots first with their forks under them. An entry the viewer
may not read is not in the list (Q10).
"""

from __future__ import annotations

import msgspec
from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import search_skill_hub_impl
from workspace_app.apps.skill_hub import (
    SkillHubEntry,
    SkillHubReview,
    SkillHubStore,
    register_skill_hub,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec


def _hub():
    spec = make_spec(default_user="system")
    register_skill_hub(spec)
    return spec, SkillHubStore(spec, MemoryFileStore())


async def _entry(
    hub: SkillHubStore,
    owner: str,
    name: str,
    description: str,
    *,
    tools: list[str] | None = None,
    forked_from: str = "",
    app: str = "rca",
) -> str:
    md = f"---\nname: {name}\ndescription: {description}\n---\n\nbody".encode()
    return await hub.publish(
        owner=owner,
        name=name,
        description=description,
        source_item="i",
        source_app=app,
        source_profile="default",
        payload={"SKILL.md": md},
        referenced_tools=tools or [],
        review=SkillHubReview(verdict="ok"),
        forked_from=forked_from,
    )


def _ctx(hub: SkillHubStore | None, *, user: str = "bob", app: str | None = "rca"):
    return RunContextWrapper(
        AgentToolContext(investigation_id="inv-1", app_slug=app, acting_user=user, skill_hub=hub)
    )


async def test_a_query_matches_name_and_description_case_insensitively():
    _spec, hub = _hub()
    hit_name = await _entry(hub, "alice", "reflow-triage", "Solder defects.")
    hit_desc = await _entry(hub, "bob", "log-digger", "Find REFLOW problems in logs.")
    miss = await _entry(hub, "carol", "deck-maker", "Slides.")

    out = await search_skill_hub_impl(_ctx(hub), "Reflow")

    assert hit_name in out and hit_desc in out and miss not in out
    assert "alice/reflow-triage" in out and "bob/log-digger" in out


async def test_an_empty_query_lists_everything_roots_first_with_forks_beneath():
    """The root is `zed/triage` and the fork `alice/triage`: name-then-owner
    order would put the fork FIRST, so only nesting puts it under its root."""
    _spec, hub = _hub()
    root = await _entry(hub, "zed", "triage", "d")
    fork = await _entry(hub, "alice", "triage", "d", forked_from=root)
    other = await _entry(hub, "carol", "zzz-last", "d")

    out = await search_skill_hub_impl(_ctx(hub), "")

    lines = [ln for ln in out.splitlines() if ln.strip()]
    i_root = next(i for i, ln in enumerate(lines) if root in ln)
    i_fork = next(i for i, ln in enumerate(lines) if fork in ln)
    i_other = next(i for i, ln in enumerate(lines) if other in ln)
    assert i_root < i_fork < i_other, "the fork sits under its root, before the next root"
    assert lines[i_fork].startswith("  ↳ ") and "fork of zed/triage" in lines[i_fork]
    assert lines[i_root].startswith("- ")


async def test_tools_this_app_lacks_are_computed_against_this_items_app():
    """`query_entity` is outside `rca`'s ceiling and inside `pm`'s. The same
    entry reads differently from the two Apps — which is the point of computing
    it here and not at publish time."""
    _spec, hub = _hub()
    entry = await _entry(hub, "alice", "triage", "d", tools=["exec", "query_entity"], app="pm")

    from_rca = await search_skill_hub_impl(_ctx(hub, app="rca"), "triage")
    from_pm = await search_skill_hub_impl(_ctx(hub, app="pm"), "triage")

    assert entry in from_rca and "query_entity" in from_rca and "this App lacks" in from_rca
    assert entry in from_pm and "this App lacks" not in from_pm


async def test_a_private_entry_is_absent_from_everyone_elses_results():
    spec, hub = _hub()
    hidden = await _entry(hub, "alice", "secret", "hush")
    rm = spec.get_resource_manager(SkillHubEntry)
    rm.update(
        hidden,
        msgspec.structs.replace(rm.get(hidden).data, permission=Permission(visibility="private")),
    )

    assert hidden not in await search_skill_hub_impl(_ctx(hub, user="bob"), "secret")
    assert hidden in await search_skill_hub_impl(_ctx(hub, user="alice"), "secret")


async def test_no_match_says_so_and_how_to_publish_one():
    _spec, hub = _hub()
    await _entry(hub, "alice", "triage", "d")

    out = await search_skill_hub_impl(_ctx(hub), "nothing-like-this")

    assert "no skill hub entry matches" in out and "publish_skill" in out


async def test_a_long_list_is_cut_and_says_how_many_more():
    _spec, hub = _hub()
    for i in range(30):
        await _entry(hub, "alice", f"skill-{i:02d}", "d")

    out = await search_skill_hub_impl(_ctx(hub), "")

    assert "skill-24" in out and "skill-25" not in out
    assert "5 more" in out


async def test_without_the_hub_the_tool_says_where_it_works():
    out = await search_skill_hub_impl(_ctx(None), "x")
    assert out.startswith("error:") and "App workspace turn" in out


async def test_a_forks_lineage_never_names_a_root_the_viewer_may_not_read():
    """Review round 1: the lineage annotation read the root with `hub.get`,
    which has no viewer — so after alice took her root private, bob's fork
    still printed "(fork of alice/secret)" to carol. Q10: what carol may not
    read is gone, so a fork of it reads "(fork)", exactly like a fork of a
    deleted root."""
    spec, hub = _hub()
    root = await _entry(hub, "alice", "secret-sauce", "d")
    fork = await _entry(hub, "bob", "secret-sauce", "d", forked_from=root)
    rm = spec.get_resource_manager(SkillHubEntry)
    rm.update(
        root,
        msgspec.structs.replace(rm.get(root).data, permission=Permission(visibility="private")),
    )

    out = await search_skill_hub_impl(_ctx(hub, user="carol"), "secret")

    assert fork in out and root not in out
    assert "alice" not in out
    assert "(fork)" in out
