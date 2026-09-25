"""#380 — the per-item skills picker resolver (`effective_item_skills`) and the
`GET /a/{slug}/items/{id}/skills` endpoint it backs.

Uses the real `_template` fixture app (declares two shared skills, default profile
opts one in) so the default-on / default-off + tri-state override paths are
exercised end to end against production loaders — no synthetic package.
"""

from __future__ import annotations

import pytest

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
    states = _by_name(effective_item_skills("_template", "default", {}, [], tools=None))
    s = states["author-skill"]
    assert s.source == "shared"
    assert s.default_on is True
    assert s.effective is True


def test_effective_item_skills_marks_a_declared_but_unopted_skill_default_off():
    """A shared skill the App declares but the profile leaves out of `skills`
    (`author-workflow`) is available-but-default-OFF: default_on False, effective
    False (still listed so the picker can offer to turn it on)."""
    states = _by_name(effective_item_skills("_template", "default", {}, [], tools=None))
    s = states["author-workflow"]
    assert s.source == "shared"
    assert s.default_on is False
    assert s.effective is False


def test_effective_item_skills_force_on_makes_a_default_off_skill_effective():
    """A per-item `skill_prefs` True flips a default-off skill effective (its
    default_on stays False — the pref is the override, not the default)."""
    states = _by_name(
        effective_item_skills("_template", "default", {"author-workflow": True}, [], tools=None)
    )
    s = states["author-workflow"]
    assert s.default_on is False
    assert s.effective is True


def test_effective_item_skills_includes_workspace_skills_as_default_on():
    """A co-created workspace skill is listed source 'workspace', default_on +
    effective — the picker surfaces it alongside the built-ins."""
    ws = [SkillMeta(name="my-skill", description="do X")]
    states = _by_name(effective_item_skills("_template", "default", {}, ws, tools=None))
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


# #589: a baked-in skill that brought files is COPIED into the workspace, and a
# workspace skill is unconditionally default-on and shadows the package source.
# Left alone, merely using a default-OFF shared skill once would silently make it
# default-ON for good: clear the per-item pref afterwards and it stays enabled,
# with nothing to explain why. A copy has to keep answering as what it is.
async def test_a_copied_baked_in_skill_keeps_its_source_and_default():
    from workspace_app.apps.skills import workspace_skill_metas

    files = await _files_with(**{"author-workflow": b"purpose only"})
    await files.write("inv", "/.skill/author-workflow/.origin", b'{"source":"shared","files":{}}')

    metas = await workspace_skill_metas(files, "inv")
    s = _by_name(effective_item_skills("_template", "default", {}, metas, tools=None))[
        "author-workflow"
    ]

    assert s.source == "shared"
    assert s.default_on is False
    assert s.effective is False


# A skill the user genuinely wrote here has no origin manifest and must keep
# behaving exactly as before — on by default, source 'workspace'.
async def test_a_hand_written_workspace_skill_is_unaffected():
    from workspace_app.apps.skills import workspace_skill_metas

    files = await _files_with(mine=b"my own")
    metas = await workspace_skill_metas(files, "inv")
    s = _by_name(effective_item_skills("_template", "default", {}, metas, tools=None))["mine"]

    assert s.source == "workspace"
    assert s.default_on is True


# The panel keys the download control off `source == "workspace"`. A copy now
# reports its package source (so it can't be silently turned on for good), which
# would take that control away — even though its files really are here and really
# are downloadable. The two facts are independent, so they are reported
# independently rather than encoded in one string.
async def test_a_copy_is_reported_as_a_local_copy_alongside_its_source():
    from workspace_app.apps.skills import workspace_skill_metas

    files = await _files_with(**{"author-workflow": b"purpose only"})
    await files.write("inv", "/.skill/author-workflow/.origin", b'{"source":"shared","files":{}}')

    metas = await workspace_skill_metas(files, "inv")
    states = _by_name(effective_item_skills("_template", "default", {}, metas, tools=None))

    assert states["author-workflow"].is_copy is True
    assert states["author-skill"].is_copy is False


# ── a copy installed from the skill hub (plan-skill-hub, review round 2) ─────


async def test_a_hub_copy_named_like_a_shared_skill_stays_a_workspace_skill():
    """`effective_item_skills` treats any copy with a package sibling as that
    package's copy — right for a materialized `author-workflow`, wrong for a
    skill somebody published to the hub under that name and this item then
    installed: its files came from the hub, its source is the workspace, and
    the panel's Publish button (offered on `source: workspace`) must stay.
    The manifest says which; the meta now carries it."""
    import msgspec

    from workspace_app.apps.skill_payload import SkillOrigin
    from workspace_app.apps.skills import workspace_skill_metas

    files = await _files_with(**{"author-workflow": b"from the hub"})
    await files.write(
        "inv",
        "/.skill/author-workflow/.origin",
        msgspec.json.encode(SkillOrigin(source="hub", files={"SKILL.md": "x"}, entry="e-1")),
    )

    metas = await workspace_skill_metas(files, "inv")
    states = _by_name(effective_item_skills("_template", "default", {}, metas, tools=None))

    assert metas[0].is_copy is True and metas[0].copy_of == "hub"
    assert states["author-workflow"].source == "workspace"
    assert states["author-workflow"].is_copy is True
    assert states["author-workflow"].copy_of == "hub"


async def test_a_copy_of_an_undeclared_package_skill_is_a_workspace_copy_of_the_package():
    """#826 review round 1: the front end took `source == "workspace" and
    is_copy` for "a hub copy" — but a copy of a package skill the App does
    NOT declare (`read_skill` materialises any skill it can resolve) has no
    package row to shadow and lists as `workspace` + `is_copy` too, so its
    Reset read 「還原成 hub 上的版本」 while the bytes came from the package.
    `copy_of` says which; the state carries it, the route sends it."""
    files = await _files_with(**{"verify-number": b"from the package"})
    await files.write("inv", "/.skill/verify-number/.origin", b'{"source":"shared","files":{}}')
    from workspace_app.apps.skills import workspace_skill_metas

    metas = await workspace_skill_metas(files, "inv")
    states = _by_name(effective_item_skills("_template", "default", {}, metas, tools=None))

    assert (states["verify-number"].source, states["verify-number"].is_copy) == ("workspace", True)
    assert states["verify-number"].copy_of == "shared"


async def test_a_package_copy_still_answers_as_the_package(monkeypatch, tmp_path):
    """The other half, unchanged: a materialized shared skill's copy keeps the
    package's source and default. And a manifest that does not decode (an
    older or hand-written `.origin` — the wrong shape, or not JSON at all) is
    still a copy of UNKNOWN source, which keeps today's package rule rather
    than inventing a hub; before P18 the listing never read the manifest, so
    a garbage one must not start breaking the index now."""
    from workspace_app.apps.skills import workspace_skill_metas

    files = await _files_with(**{"author-workflow": b"purpose only"})
    await files.write("inv", "/.skill/author-workflow/.origin", b'{"source":"shared","files":{}}')
    metas = await workspace_skill_metas(files, "inv")
    assert metas[0].copy_of == "shared"
    states = _by_name(effective_item_skills("_template", "default", {}, metas, tools=None))
    assert states["author-workflow"].source == "shared"

    for manifest in (b"{}", b"not json at all"):
        await files.write("inv", "/.skill/author-workflow/.origin", manifest)
        metas = await workspace_skill_metas(files, "inv")
        assert (metas[0].is_copy, metas[0].copy_of) == (True, ""), manifest
        states = _by_name(effective_item_skills("_template", "default", {}, metas, tools=None))
        assert states["author-workflow"].source == "shared"


async def test_a_manifest_gone_between_the_listing_and_the_read_is_simply_not_a_copy(
    monkeypatch,
):
    """Round 3: the listing never READ `.origin` before P18; batching the reads
    strictly put the whole index at the mercy of one manifest deleted between
    `ls` and the read (the publish reply itself tells people to `delete_file`
    it). `read_all_existing` exists for exactly that race: gone means not a
    copy, and the rest of the index still renders."""
    from workspace_app.apps.skills import workspace_skill_metas

    files = await _files_with(alpha=b"a", beta=b"b")
    await files.write("inv", "/.skill/alpha/.origin", b'{"source":"shared","files":{}}')
    real_ls = files.ls

    async def ls_then_delete(workspace_id: str, prefix: str = "") -> list[str]:
        paths = await real_ls(workspace_id, prefix)
        await files.delete(workspace_id, "/.skill/alpha/.origin")
        return paths

    monkeypatch.setattr(files, "ls", ls_then_delete)

    metas = await workspace_skill_metas(files, "inv")

    assert [(m.name, m.is_copy, m.copy_of) for m in metas] == [
        ("alpha", False, ""),
        ("beta", False, ""),
    ]


async def test_a_skill_md_gone_between_the_listing_and_the_read_still_raises(monkeypatch):
    """The other half of the one-batch read, pinned (round 4 found the strict
    `raise` a hand-written line no test held): a manifest that vanished makes
    its folder not a copy, but a SKILL.md that vanished is what it always was
    — a `FileNotFound` out of the index, the tolerance the per-file loop
    never had and this batch was told not to invent."""
    from workspace_app.apps.skills import workspace_skill_metas
    from workspace_app.filestore.protocol import FileNotFound

    files = await _files_with(alpha=b"a", beta=b"b")
    real_ls = files.ls

    async def ls_then_delete(workspace_id: str, prefix: str = "") -> list[str]:
        paths = await real_ls(workspace_id, prefix)
        await files.delete(workspace_id, "/.skill/alpha/SKILL.md")
        return paths

    monkeypatch.setattr(files, "ls", ls_then_delete)

    with pytest.raises(FileNotFound):
        await workspace_skill_metas(files, "inv")
