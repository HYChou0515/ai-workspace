"""#589 — a baked-in skill's files land in the workspace when the skill is used.

Delivering a skill's body to the model is the moment its instructions start
referring to `scripts/summarise.py`, so it is the moment those files have to
exist. Before this, they never existed at all: committing one next to SKILL.md
was a no-op with no error and no log.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import workspace_app.apps.skills as skills_mod
from workspace_app.apps.skills import (
    refresh_skill,
    resolve_skill_body,
    skill_update_available,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


@pytest.fixture
def isolated_apps(tmp_path: Path, monkeypatch):
    """A throwaway apps package so a profile can ship whatever skill we need."""
    root = tmp_path / "apps_root"
    pkg = root / "tplpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    importlib.reload(skills_mod)
    monkeypatch.setattr(skills_mod, "_APPS_PKG", "tplpkg")
    skills_mod.list_skills.cache_clear()
    skills_mod.load_skill.cache_clear()
    yield pkg
    skills_mod.list_skills.cache_clear()
    skills_mod.load_skill.cache_clear()
    sys.modules.pop("tplpkg", None)


def _profile_skill(root: Path, name: str, *, files: dict[str, str]) -> Path:
    sd = root / "rca" / "profiles" / "local-lab" / ".skill" / name
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nrun scripts/x.py")
    for rel, text in files.items():
        target = sd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return sd


async def test_using_a_skill_puts_its_scripts_in_the_workspace(isolated_apps: Path):
    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('hi')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"

    body = await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    assert body is not None and "run scripts/x.py" in body
    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"print('hi')\n"


# The reason the files go into the workspace at all is that the AI is meant to
# tweak them — that is what separates a skill from a tool. So using the skill
# again must never restore the shipped bytes over the edited ones. Refreshing a
# copy is a separate, explicit action, never a side effect of use.
async def test_never_overwrites_an_edited_copy(isolated_apps: Path):
    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('shipped')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.write(inv, "/.skill/triage/scripts/x.py", b"print('the AI improved this')\n")

    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    kept = await files.read(inv, "/.skill/triage/scripts/x.py")
    assert kept == b"print('the AI improved this')\n"


# Copying a skill has real consequences: the copy shadows the package version, so
# its body stops tracking upstream and it starts reporting as a workspace skill.
# For a skill that is nothing BUT its SKILL.md, that is all cost and no benefit —
# the copy would be byte-identical to what the package already serves. Every
# skill shipped today is exactly that, so the default has to be "leave it alone".
async def test_a_skill_with_no_files_of_its_own_is_left_in_the_package(isolated_apps: Path):
    _profile_skill(isolated_apps, "plain", files={})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"

    body = await resolve_skill_body(files, inv, "rca", "local-lab", "plain")

    assert body is not None
    assert await files.ls(inv, "/.skill/plain/") == []


# The copy has to remember where it came from, or nothing can ever tell it apart
# from a skill the user wrote by hand — which is what decides whether a newer
# version exists upstream and whether a given file still holds the shipped bytes.
async def test_the_copy_records_where_it_came_from(isolated_apps: Path):
    import msgspec

    from workspace_app.apps.skill_payload import SkillOrigin

    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('hi')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"

    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    origin = msgspec.json.decode(await files.read(inv, "/.skill/triage/.origin"), type=SkillOrigin)

    assert origin.source == "profile"
    assert sorted(origin.files) == ["SKILL.md", "scripts/x.py"]


# `read_skill` carries its own copy of the source precedence, so wiring only the
# apply path would leave the files missing exactly when the model went looking
# for them on its own — the commonest way a skill actually gets used.
async def test_the_read_skill_tool_materializes_too(isolated_apps: Path):
    from agents import RunContextWrapper

    from workspace_app.agent.context import AgentToolContext
    from workspace_app.agent.tools import read_skill_impl

    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('hi')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    ctx = AgentToolContext(
        app_slug="rca", template_profile="local-lab", files=files, investigation_id=inv
    )

    await read_skill_impl(RunContextWrapper(ctx), "triage")

    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"print('hi')\n"


# ─── refreshing a copy from upstream (#589 P5) ───────────────────────


async def test_refresh_brings_an_untouched_file_up_to_the_shipped_version(isolated_apps: Path):
    sd = _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    (sd / "scripts" / "x.py").write_text("v2\n")
    result = await refresh_skill(files, inv, "rca", "local-lab", "triage")

    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"v2\n"
    assert result.updated == ["scripts/x.py"]


# The people who use this feature exactly as intended — letting the AI tweak the
# scripts — are the ones an overwriting update would hurt most. One press and
# every tweak is gone, at the moment they least expect it.
async def test_refresh_leaves_an_edited_file_alone_and_says_so(isolated_apps: Path):
    sd = _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.write(inv, "/.skill/triage/scripts/x.py", b"the AI improved this\n")

    (sd / "scripts" / "x.py").write_text("v2\n")
    result = await refresh_skill(files, inv, "rca", "local-lab", "triage")

    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"the AI improved this\n"
    assert result.skipped == ["scripts/x.py"]
    assert result.updated == []


# A new version can add and drop files, not just change them. Dropping follows
# the same rule as changing: a file the AI edited is its work now, so upstream
# removing the original does not license deleting it.
async def test_refresh_adds_new_files_and_drops_retired_untouched_ones(isolated_apps: Path):
    sd = _profile_skill(
        isolated_apps, "triage", files={"scripts/old.py": "old\n", "scripts/kept.py": "kept\n"}
    )
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.write(inv, "/.skill/triage/scripts/kept.py", b"the AI improved this\n")

    (sd / "scripts" / "old.py").unlink()
    (sd / "scripts" / "kept.py").unlink()
    (sd / "scripts" / "new.py").write_text("new\n")
    result = await refresh_skill(files, inv, "rca", "local-lab", "triage")

    assert await files.read(inv, "/.skill/triage/scripts/new.py") == b"new\n"
    assert result.removed == ["scripts/old.py"]
    # Retired upstream, but edited here — still the AI's work, so it stays.
    assert await files.read(inv, "/.skill/triage/scripts/kept.py") == b"the AI improved this\n"
    assert result.skipped == ["scripts/kept.py"]


# The escape hatch for everything the per-file rule refuses to touch. It is
# destructive on purpose, and only ever because the user said so — never as a
# side effect of using or updating the skill.
async def test_reset_to_factory_overwrites_even_edited_files(isolated_apps: Path):
    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "shipped\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.write(inv, "/.skill/triage/scripts/x.py", b"the AI improved this\n")
    await files.write(inv, "/.skill/triage/scripts/stray.py", b"invented here\n")

    result = await refresh_skill(files, inv, "rca", "local-lab", "triage", force=True)

    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"shipped\n"
    assert result.skipped == []
    assert "scripts/x.py" in result.updated


# The other half of the ask: a skill added under `sample-skills/`, shared across
# apps rather than baked into one profile. It goes through a different registry
# and a different loader, so "the profile path works" says nothing about it —
# and shipping this one untested would repeat the exact mistake being fixed,
# where a whole source did nothing and nobody noticed.
async def test_a_shared_skill_ships_its_files_too(tmp_path: Path, monkeypatch):
    from workspace_app.apps import shared_skills

    src = tmp_path / "triage-shared"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: triage-shared\ndescription: d\n---\n\nrun it")
    (src / "scripts" / "x.py").write_text("print('shared')\n")
    monkeypatch.setitem(shared_skills.SHARED_SKILLS, "triage-shared", src)

    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    body = await resolve_skill_body(files, inv, None, None, "triage-shared")

    assert body is not None and "run it" in body
    assert await files.read(inv, "/.skill/triage-shared/scripts/x.py") == b"print('shared')\n"


# Without this the refresh control is a coin flip: it shows on every copy, and
# pressing it when upstream has not moved does nothing visible. "Nothing
# happened" is indistinguishable from "it is broken".
async def test_reports_whether_upstream_has_moved_since_the_copy_was_made(isolated_apps: Path):
    sd = _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    assert await skill_update_available(files, inv, "rca", "local-lab", "triage") is False

    (sd / "scripts" / "x.py").write_text("v2\n")
    assert await skill_update_available(files, inv, "rca", "local-lab", "triage") is True


# Editing a file here is not an upstream change. Offering "update" for it would
# invite the user to press a button whose only honest outcome is "skipped".
async def test_a_local_edit_is_not_an_upstream_update(isolated_apps: Path):
    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.write(inv, "/.skill/triage/scripts/x.py", b"the AI improved this\n")

    assert await skill_update_available(files, inv, "rca", "local-lab", "triage") is False


# The shape this feature was actually asked for: a real binary asset — a .pptx
# template — that a script in the same skill opens and fills in. Nothing about it
# is executable, so the "no exec bit" limit does not apply; what matters is that
# the bytes survive verbatim, through a pipeline that had never carried anything
# but markdown.
async def test_a_binary_template_survives_byte_for_byte(isolated_apps: Path):
    import zipfile

    sd = _profile_skill(isolated_apps, "deck", files={"scripts/make.py": "from pptx import *\n"})
    # A .pptx IS a zip of XML parts — real binary, with a header and null bytes.
    tpl = sd / "assets" / "template.pptx"
    tpl.parent.mkdir()
    with zipfile.ZipFile(tpl, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/media/image1.png", bytes(range(256)))
    original = tpl.read_bytes()

    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "deck")

    landed = await files.read(inv, "/.skill/deck/assets/template.pptx")
    assert landed == original
    # Still a readable archive on the other side, not just equal bytes by luck.
    import io

    assert zipfile.ZipFile(io.BytesIO(landed)).read("ppt/media/image1.png") == bytes(range(256))


# A skill written here has no upstream, so there is nothing to be behind and
# nothing to pull. Both operations have to say so quietly rather than throw.
async def test_a_hand_written_skill_has_nothing_to_refresh(isolated_apps: Path):
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    md = b"---\nname: mine\ndescription: d\n---\n\nmine"
    await files.write(inv, "/.skill/mine/SKILL.md", md)

    assert await skill_update_available(files, inv, "rca", "local-lab", "mine") is False
    nothing = await refresh_skill(files, inv, "rca", "local-lab", "mine")
    assert (nothing.updated, nothing.skipped, nothing.removed) == ([], [], [])


# A skill can be retired from the package while copies of it live on. The copy is
# the workspace's own now — it keeps working, it simply has no upstream left to
# compare against or pull from.
async def test_a_copy_outlives_the_skill_being_retired_upstream(isolated_apps: Path):
    sd = _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    for child in sorted(sd.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    sd.rmdir()

    assert await skill_update_available(files, inv, "rca", "local-lab", "triage") is False
    assert (await refresh_skill(files, inv, "rca", "local-lab", "triage")).updated == []
    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"v1\n"


# Deleting a file is an edit like any other. Upstream changing it afterwards does
# not license bringing it back — the AI removed it on purpose.
async def test_a_file_the_ai_deleted_is_not_quietly_restored(isolated_apps: Path):
    sd = _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.delete(inv, "/.skill/triage/scripts/x.py")

    (sd / "scripts" / "x.py").write_text("v2\n")
    result = await refresh_skill(files, inv, "rca", "local-lab", "triage")

    assert result.skipped == ["scripts/x.py"]
    assert result.updated == []


# A profile that ships skills of its own must not shadow a shared skill it does
# not have: the lookup falls through rather than stopping at the profile.
async def test_a_shared_skill_is_found_even_when_the_profile_ships_others(
    isolated_apps: Path, tmp_path: Path, monkeypatch
):
    from workspace_app.apps import shared_skills

    _profile_skill(isolated_apps, "other", files={"scripts/y.py": "y\n"})
    src = tmp_path / "shared-one"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: shared-one\ndescription: d\n---\n\nshared body")
    (src / "scripts" / "z.py").write_text("z\n")
    monkeypatch.setitem(shared_skills.SHARED_SKILLS, "shared-one", src)

    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "shared-one")

    assert await files.read(inv, "/.skill/shared-one/scripts/z.py") == b"z\n"
