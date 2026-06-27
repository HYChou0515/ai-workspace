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
"""

from __future__ import annotations

from pathlib import Path

from .skills import SKILL_BODY_CAP, SkillError, SkillMeta, _parse_frontmatter

_REPO = Path(__file__).resolve().parents[3]
SHARED_SKILLS_DIR = _REPO / "sample-skills"

# {skill name → source dir holding SKILL.md (+ optional references/ scripts/)}.
SHARED_SKILLS: dict[str, Path] = {
    "author-skill": SHARED_SKILLS_DIR / "author-skill",
}


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
    """A shared skill's body markdown (frontmatter stripped). Raises ``SkillError``
    on an unregistered name, a missing SKILL.md, or a body over the cap."""
    src = SHARED_SKILLS.get(name)
    skill_md = None if src is None else src / "SKILL.md"
    if skill_md is None or not skill_md.is_file():
        avail = ", ".join(sorted(SHARED_SKILLS)) or "(none)"
        raise SkillError(f"unknown shared skill {name!r}. available: {avail}")
    _front, body = _parse_frontmatter(skill_md.read_bytes())
    if len(body) > SKILL_BODY_CAP:
        raise SkillError(f"shared skill {name!r} body exceeds {SKILL_BODY_CAP} chars ({len(body)})")
    return body


def _meta(name: str) -> SkillMeta | None:
    src = SHARED_SKILLS.get(name)
    if src is None:
        return None
    skill_md = src / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        front, _body = _parse_frontmatter(skill_md.read_bytes())
    except SkillError:
        return None
    n = str(front.get("name", "")).strip()
    description = str(front.get("description", "")).strip()
    if not n or n != name:
        return None
    return SkillMeta(name=n, description=description)
