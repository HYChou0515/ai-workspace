"""A view plugin's skill joins the shared skills (#847/#848 PR1 P7).

Registered wholesale at composition (``create_app``) and by the CLIs that
compose a turn without an app (``skill_eval``, ``view_plugin tune``), from the
same discovered plugin list. Who then GETS a plugin skill is decided per item by
``apps.shared_skills.plugin_skills_for`` — any item whose resolved tools can
draw a view — and ``skill_prefs`` can still turn it off per item.

A ``SKILL.md``-only skill is never copied into a workspace, so an operator's
edit to ``<dir>/<name>/skill/SKILL.md`` reaches the next turn and the next
``view_plugin tune`` run. A skill with other files is copied on first
``read_skill`` and that copy wins until the skills panel's Refresh (#589).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from workspace_app.apps import shared_skills, skills
from workspace_app.view_plugins.discovery import ViewPlugin, ViewPluginError


def plugin_skill_sources(plugins: Sequence[ViewPlugin]) -> dict[str, Path]:
    """``{skill name: folder}`` for the plugins that ship a skill. The skill is
    named after its plugin, and its ``SKILL.md`` must say so: a mismatched
    frontmatter ``name`` would otherwise be dropped from every index in silence.
    A name a shipped shared skill already has refuses boot too."""
    out: dict[str, Path] = {}
    for p in plugins:
        folder = p.skill_dir
        if folder is None:
            continue
        skill_md = folder / "SKILL.md"
        try:
            front, _ = skills._parse_frontmatter(skill_md.read_bytes())
        except skills.SkillError as e:
            raise ViewPluginError(f"view plugin {p.name!r} ({skill_md}): {e}") from e
        name = str(front.get("name", "")).strip()
        if name != p.name:
            raise ViewPluginError(
                f"view plugin {p.name!r} ({skill_md}): the skill's frontmatter name is "
                f"{name!r} — it must be the plugin's name, {p.name!r}"
            )
        if not str(front.get("description", "")).strip():
            raise ViewPluginError(
                f"view plugin {p.name!r} ({skill_md}): the skill needs a `description` — "
                "it is the line agents choose the skill by"
            )
        if p.name in shared_skills.SHARED_SKILLS:
            raise ViewPluginError(
                f"view plugin {p.name!r}: a shipped shared skill is already called "
                f"{p.name!r} — rename the plugin"
            )
        out[p.name] = folder
    return out


def register_plugin_skills(plugins: Sequence[ViewPlugin]) -> None:
    shared_skills.set_plugin_skills(plugin_skill_sources(plugins))
