"""#298 — workspace-scoped skills (`<workspace>/.skill/<name>/SKILL.md`):

- `workspace_skill_metas(files, workspace_id)` → sorted `SkillMeta`, skipping
  malformed / name-mismatched skills (same tolerance as the package loader).
- `load_workspace_skill(files, workspace_id, name)` → body or None, body-cap raises.
- Both are read LIVE (never cached) — the workspace is hand-editable + may have
  just been written this turn.
"""

from __future__ import annotations

import pytest

import workspace_app.apps.skills as skills_mod
from workspace_app.apps.skills import (
    SKILL_BODY_CAP,
    SkillMeta,
    build_workspace_skills_block,
    load_workspace_skill,
    workspace_skill_metas,
    workspace_skills_block,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _files() -> tuple[WorkspaceFiles, str]:
    return WorkspaceFiles(MemoryFileStore()), "inv-1"


async def _put(files: WorkspaceFiles, inv: str, name: str, description: str, body: str) -> None:
    md = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    await files.write(inv, f"/.skill/{name}/SKILL.md", md.encode("utf-8"))


async def test_metas_lists_well_formed_skills_sorted_by_name():
    files, inv = _files()
    await _put(files, inv, "zebra", "z thing", "body z")
    await _put(files, inv, "alpha", "a thing", "body a")
    metas = await workspace_skill_metas(files, inv)
    assert [(m.name, m.description) for m in metas] == [
        ("alpha", "a thing"),
        ("zebra", "z thing"),
    ]


async def test_metas_empty_when_no_skill_dir():
    files, inv = _files()
    assert await workspace_skill_metas(files, inv) == []


async def test_metas_skips_name_dir_mismatch():
    """A frontmatter name that disagrees with the folder is skipped — same rule
    the package loader enforces, so `read_skill(name)` can't be ambiguous."""
    files, inv = _files()
    # folder `good` but frontmatter says `other`
    md = "---\nname: other\ndescription: d\n---\n\nbody"
    await files.write(inv, "/.skill/good/SKILL.md", md.encode("utf-8"))
    await _put(files, inv, "ok", "fine", "body")
    assert [m.name for m in await workspace_skill_metas(files, inv)] == ["ok"]


async def test_metas_skips_missing_name():
    files, inv = _files()
    md = "---\ndescription: d\n---\n\nbody"
    await files.write(inv, "/.skill/nameless/SKILL.md", md.encode("utf-8"))
    assert await workspace_skill_metas(files, inv) == []


async def test_metas_ignores_nested_skill_files():
    """Only `.skill/<name>/SKILL.md` is a skill — a deeper reference/script file
    under a skill folder isn't a second skill."""
    files, inv = _files()
    await _put(files, inv, "foo", "f", "body")
    await files.write(inv, "/.skill/foo/references/x.md", b"# ref")
    assert [m.name for m in await workspace_skill_metas(files, inv)] == ["foo"]


async def test_metas_skips_malformed_frontmatter():
    """An unparseable frontmatter is skipped (logged), not fatal — one bad
    hand-edit can't break the whole index."""
    files, inv = _files()
    bad = "---\ndescription: [unbalanced\n---\n\nbody"
    await files.write(inv, "/.skill/broken/SKILL.md", bad.encode("utf-8"))
    await _put(files, inv, "ok", "fine", "body")
    assert [m.name for m in await workspace_skill_metas(files, inv)] == ["ok"]


async def test_metas_is_live_not_cached():
    """A skill written after a first read shows up on the next read (Q3a)."""
    files, inv = _files()
    assert await workspace_skill_metas(files, inv) == []
    await _put(files, inv, "fresh", "new", "body")
    assert [m.name for m in await workspace_skill_metas(files, inv)] == ["fresh"]


async def test_load_workspace_skill_returns_body_without_frontmatter():
    files, inv = _files()
    await _put(files, inv, "s", "d", "# Heading\n\ncontent")
    body = await load_workspace_skill(files, inv, "s")
    assert body == "# Heading\n\ncontent"


async def test_load_workspace_skill_none_when_absent():
    files, inv = _files()
    assert await load_workspace_skill(files, inv, "nope") is None


async def test_load_workspace_skill_body_cap_raises():
    files, inv = _files()
    await _put(files, inv, "huge", "d", "x" * (SKILL_BODY_CAP + 1))
    # Reference SkillError through the live module: another test file reloads
    # `skills` (for its cache), which rebinds the class — a captured top-level
    # import would no longer match what `load_workspace_skill` raises.
    with pytest.raises(skills_mod.SkillError):
        await load_workspace_skill(files, inv, "huge")


def test_block_empty_for_no_skills():
    assert workspace_skills_block([]) == ""


def test_block_lists_skills_with_read_skill_hint():
    out = workspace_skills_block([SkillMeta("a", "does a"), SkillMeta("b", "does b")])
    assert "read_skill" in out
    assert "- `a`: does a" in out
    assert "- `b`: does b" in out


async def test_build_block_reads_workspace_live():
    files, inv = _files()
    assert await build_workspace_skills_block(files, inv) == ""
    await _put(files, inv, "fresh", "new", "body")
    out = await build_workspace_skills_block(files, inv)
    assert "- `fresh`: new" in out
