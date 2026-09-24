"""Shared (built-in) skills — introduced exactly like tool-packages (#298 Q7).

A ``SHARED_SKILLS`` registry maps a skill name to its source dir (mirroring
``workspace_app.tooling.packages.PACKAGES``); an App opts in by listing the name
in ``app.json`` ``agent.skills`` (parallel to ``agent.tools``). Unlike a
tool-package there is **no prebuild** — a skill is plain markdown read at
prompt-compose time. A real deployment replaces ``SHARED_SKILLS`` with its own
dict, same as ``PACKAGES``.

``author-skill`` is the one v1 ships: the meta-skill that teaches the agent to
co-author a skill with the user (the heart of #298). Source lives under
``sample-skills/`` at the repo root, mirroring ``sample-tools/``.

Everything is referenced through the live ``skills`` module (``skills.SkillError``
etc.) rather than imported by name, so a test that reloads ``skills`` (to reset its
``@cache``) doesn't leave us catching/raising a stale exception class.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from . import skills

if TYPE_CHECKING:
    from .skills import SkillMeta

_REPO = Path(__file__).resolve().parents[3]
SHARED_SKILLS_DIR = _REPO / "sample-skills"

# {skill name → source dir holding SKILL.md (+ optional references/ scripts/)}.
SHARED_SKILLS: dict[str, Path] = {
    "author-skill": SHARED_SKILLS_DIR / "author-skill",
    # #323: the meta-skill that teaches the agent to co-author a *workflow* (a runnable
    # workflow.json) with the user — the workflow analogue of author-skill.
    "author-workflow": SHARED_SKILLS_DIR / "author-workflow",
    # grill-me: interview the user one decision at a time instead of guessing.
    # Pairs with the `ask_user` tool — without it the agent can only ask in
    # prose, which is what made this skill not worth having before.
    "grill-me": SHARED_SKILLS_DIR / "grill-me",
    # verify-number: hand over a computed number together with the evidence that
    # makes it checkable. Nobody can verify a number by reading it, and until this
    # no prompt in the system said anything about computational correctness.
    # Tune it against your own model with `python -m workspace_app.skill_eval`.
    "verify-number": SHARED_SKILLS_DIR / "verify-number",
    # wui: build a folder in the item's workspace that renders as a live page.
    # Registered but declared by NO app yet — the renderer ships to everyone, so
    # a hand-written `view: wui` file already works, and what is held back is
    # whether the agent proposes one. An app opts in with a line in its
    # `agent.skills` when it is ready to have people find this.
    "wui": SHARED_SKILLS_DIR / "wui",
    # skill-hub (docs/plan-skill-hub.md): when to search, install or publish a
    # skill, and what to tell the user around each — the three tools' guidance.
    # Declared by the apps that grant the three tools.
    "skill-hub": SHARED_SKILLS_DIR / "skill-hub",
}


# #847/#848: each runtime view plugin's `skill/` joins the shared skills. Set
# WHOLESALE at composition (`view_plugins.skills.register_plugin_skills`), never
# appended to, so a second `create_app` in one process (every test) starts from
# what IT discovered. Kept apart from `SHARED_SKILLS` because the two differ in
# who gets them: an app DECLARES a shipped skill in `agent.skills`, while a
# plugin skill reaches every item that can draw a view (`plugin_skills_for`).
PLUGIN_SKILLS: dict[str, Path] = {}

#: The tools that make an item able to draw a view: write the `*.ai.yaml`, then
#: show it. Plugin skills and the `## Available views` index go to exactly the
#: items whose RESOLVED tool set holds both.
VIEW_DRAWING_TOOLS = frozenset({"write_file", "show_file"})


#: `(kind, when)` for each view plugin `views` entry — the lines of the
#: `## Available views` prompt index. Set with the plugin skills.
PLUGIN_VIEWS: list[tuple[str, str]] = []


def set_plugin_views(views: Sequence[tuple[str, str]]) -> None:
    PLUGIN_VIEWS[:] = list(views)


def plugin_views_for(tools: Collection[str] | None) -> list[tuple[str, str]]:
    """The `## Available views` lines an item with this RESOLVED tool set gets."""
    return list(PLUGIN_VIEWS) if can_draw_views(tools) else []


def set_plugin_skills(sources: Mapping[str, Path]) -> None:
    PLUGIN_SKILLS.clear()
    PLUGIN_SKILLS.update(sources)


def can_draw_views(tools: Collection[str] | None) -> bool:
    """``None`` is an unrestricted tool set (``allowed_tools=None``), which holds
    everything."""
    return tools is None or set(tools) >= VIEW_DRAWING_TOOLS


def shared_skill_source(name: str) -> Path | None:
    """Where a shared skill's folder is — a shipped one or a view plugin's."""
    return SHARED_SKILLS.get(name) or PLUGIN_SKILLS.get(name)


def plugin_skills_for(tools: Collection[str] | None) -> list[SkillMeta]:
    """The plugin skills an item with this RESOLVED tool set gets."""
    return shared_skill_metas(sorted(PLUGIN_SKILLS)) if can_draw_views(tools) else []


def shared_skill_metas(names: list[str]) -> list[SkillMeta]:
    """``(name, description)`` for each declared shared skill that resolves to a
    well-formed SKILL.md, in the given order. Names absent from the registry, or
    whose frontmatter is malformed / nameless / name-mismatched, are skipped — the
    manifest coherence check (`validate_*`) is the loud guard for a typo."""
    out: list[SkillMeta] = []
    for name in names:
        meta = _meta(name)
        if meta is not None:
            out.append(meta)
    return out


def load_shared_skill(name: str) -> str:
    """A shared skill's body markdown (frontmatter stripped). Raises
    ``skills.SkillError`` on an unregistered name, a missing SKILL.md, or a body
    over the cap."""
    src = shared_skill_source(name)
    skill_md = None if src is None else src / "SKILL.md"
    if skill_md is None or not skill_md.is_file():
        avail = ", ".join(sorted({*SHARED_SKILLS, *PLUGIN_SKILLS})) or "(none)"
        raise skills.SkillError(f"unknown shared skill {name!r}. available: {avail}")
    _front, body = skills._parse_frontmatter(skill_md.read_bytes())
    if len(body) > skills.SKILL_BODY_CAP:
        raise skills.SkillError(
            f"shared skill {name!r} body exceeds {skills.SKILL_BODY_CAP} chars ({len(body)})"
        )
    return body


def _meta(name: str) -> SkillMeta | None:
    src = shared_skill_source(name)
    if src is None:
        return None
    skill_md = src / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        front, _body = skills._parse_frontmatter(skill_md.read_bytes())
    except skills.SkillError:
        return None
    n = str(front.get("name", "")).strip()
    description = str(front.get("description", "")).strip()
    if not n or n != name:
        return None
    return skills.SkillMeta(name=n, description=description)
