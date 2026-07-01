"""#380 — the per-item skills picker resolver (`effective_item_skills`) and the
`GET /a/{slug}/items/{id}/skills` endpoint it backs.

Uses the real `_template` fixture app (declares two shared skills, default profile
opts one in) so the default-on / default-off + tri-state override paths are
exercised end to end against production loaders — no synthetic package.
"""

from __future__ import annotations

from workspace_app.apps.skills import (
    SkillMeta,
    build_applied_skills_block,
    effective_item_skills,
    resolve_skill_body,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _by_name(states):
    return {s.name: s for s in states}


async def _files_with(**path_bodies: bytes) -> WorkspaceFiles:
    files = WorkspaceFiles(MemoryFileStore())
    for name, body in path_bodies.items():
        md = b"---\nname: " + name.encode() + b"\ndescription: d\n---\n\n" + body
        await files.write("inv", f"/.skill/{name}/SKILL.md", md)
    return files


def test_effective_item_skills_marks_a_profile_opted_in_shared_skill_default_on():
    """A shared skill the default profile opts into (`author-skill`) is source
    'shared', default_on, and effective with no per-item override."""
    states = _by_name(effective_item_skills("_template", "default", {}, []))
    s = states["author-skill"]
    assert s.source == "shared"
    assert s.default_on is True
    assert s.effective is True


def test_effective_item_skills_marks_a_declared_but_unopted_skill_default_off():
    """A shared skill the App declares but the profile leaves out of `skills`
    (`author-workflow`) is available-but-default-OFF: default_on False, effective
    False (still listed so the picker can offer to turn it on)."""
    states = _by_name(effective_item_skills("_template", "default", {}, []))
    s = states["author-workflow"]
    assert s.source == "shared"
    assert s.default_on is False
    assert s.effective is False


def test_effective_item_skills_force_on_makes_a_default_off_skill_effective():
    """A per-item `skill_prefs` True flips a default-off skill effective (its
    default_on stays False — the pref is the override, not the default)."""
    states = _by_name(effective_item_skills("_template", "default", {"author-workflow": True}, []))
    s = states["author-workflow"]
    assert s.default_on is False
    assert s.effective is True


def test_effective_item_skills_includes_workspace_skills_as_default_on():
    """A co-created workspace skill is listed source 'workspace', default_on +
    effective — the picker surfaces it alongside the built-ins."""
    ws = [SkillMeta(name="my-skill", description="do X")]
    states = _by_name(effective_item_skills("_template", "default", {}, ws))
    s = states["my-skill"]
    assert s.source == "workspace"
    assert s.default_on is True
    assert s.effective is True


# ─── apply-this-turn body resolution (#380 P3) ────────────────────────


async def test_resolve_skill_body_prefers_a_workspace_skill():
    """A workspace `.skill/` shadows any package/shared skill of the same name."""
    files = await _files_with(w=b"WSBODY")
    assert await resolve_skill_body(files, "inv", "rca", "default", "w") == "WSBODY"


async def test_resolve_skill_body_falls_back_to_a_shared_skill(monkeypatch, tmp_path):
    import workspace_app.apps.shared_skills as shared

    d = tmp_path / "author-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: author-skill\ndescription: m\n---\n\nSHAREDBODY")
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"author-skill": d})
    files = WorkspaceFiles(MemoryFileStore())
    assert await resolve_skill_body(files, "inv", "rca", "default", "author-skill") == "SHAREDBODY"


async def test_resolve_skill_body_falls_back_to_a_package_skill():
    """`rca/local-lab` ships the `report-format` package skill — resolved last."""
    files = WorkspaceFiles(MemoryFileStore())
    body = await resolve_skill_body(files, "inv", "rca", "local-lab", "report-format")
    assert body is not None and body != ""


async def test_resolve_skill_body_none_for_an_unknown_name():
    files = WorkspaceFiles(MemoryFileStore())
    assert await resolve_skill_body(files, "inv", "rca", "default", "ghost") is None


async def test_resolve_skill_body_none_without_app_or_profile():
    files = WorkspaceFiles(MemoryFileStore())
    assert await resolve_skill_body(files, "inv", None, None, "x") is None


async def test_build_applied_skills_block_empty_when_no_names():
    files = WorkspaceFiles(MemoryFileStore())
    assert await build_applied_skills_block(files, "inv", "rca", "default", []) == ""


async def test_build_applied_skills_block_renders_body_under_a_heading():
    files = await _files_with(w=b"HELLOBODY")
    out = await build_applied_skills_block(files, "inv", "rca", "default", ["w"])
    assert "Apply these skills now" in out
    assert "### w" in out
    assert "HELLOBODY" in out


async def test_build_applied_skills_block_notes_an_unknown_skill():
    files = WorkspaceFiles(MemoryFileStore())
    out = await build_applied_skills_block(files, "inv", "rca", "default", ["ghost"])
    assert "ghost" in out
    assert "not found" in out


async def test_build_applied_skills_block_notes_a_body_over_cap(monkeypatch):
    import workspace_app.apps.skills as skills

    monkeypatch.setattr(skills, "SKILL_BODY_CAP", 5)
    files = await _files_with(big=b"x" * 50)
    out = await build_applied_skills_block(files, "inv", "rca", "default", ["big"])
    assert "big" in out
    assert "could not load" in out
