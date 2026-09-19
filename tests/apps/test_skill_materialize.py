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
    install_hub_skill,
    refresh_skill,
    resolve_skill_body,
    skill_upstream,
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


async def test_author_skill_s_writing_rules_land_in_the_workspace_on_read():
    """The real registry, not a synthetic one: `author-skill`'s body tells the
    agent to read `references/writing-for-agents.md` (the tidy-before-save
    rules), so resolving the skill has to put that file where `read_file` can
    open it — a pointer to a file that is not there is the silent-no-op the
    rules themselves warn about."""
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    body = await resolve_skill_body(files, inv, None, None, "author-skill")

    assert body is not None and "references/writing-for-agents.md" in body
    rules = await files.read(inv, "/.skill/author-skill/references/writing-for-agents.md")
    assert b"Leading word" in rules


# Without this the refresh control is a coin flip: it shows on every copy, and
# pressing it when upstream has not moved does nothing visible. "Nothing
# happened" is indistinguishable from "it is broken".
async def test_reports_whether_upstream_has_moved_since_the_copy_was_made(isolated_apps: Path):
    sd = _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    up = await skill_upstream(files, inv, "rca", "local-lab", "triage")
    assert up is not None and up.update_available is False

    (sd / "scripts" / "x.py").write_text("v2\n")
    up = await skill_upstream(files, inv, "rca", "local-lab", "triage")
    assert up is not None and up.update_available is True


# Editing a file here is not an upstream change. Offering "update" for it would
# invite the user to press a button whose only honest outcome is "skipped".
async def test_a_local_edit_is_not_an_upstream_update(isolated_apps: Path):
    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "v1\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.write(inv, "/.skill/triage/scripts/x.py", b"the AI improved this\n")

    up = await skill_upstream(files, inv, "rca", "local-lab", "triage")
    assert up is not None and up.update_available is False


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

    assert await skill_upstream(files, inv, "rca", "local-lab", "mine") is None
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

    upstream = await skill_upstream(files, inv, "rca", "local-lab", "triage")
    assert upstream is not None
    assert (upstream.state, upstream.update_available) == ("deleted", False)
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


# ── the skill hub as a third upstream (plan P5) ──────────────────────────────
#
# A copy installed from the skill hub has an upstream that can do two things a
# package skill cannot: be taken down (the owner made it private) and be
# deleted. Both are reported as a STATE next to `update_available`, never by
# raising — the Skills panel lists every copy, and one dead upstream must not
# take the panel down with it.


def _hub():
    from workspace_app.apps.skill_hub import SkillHubStore, register_skill_hub
    from workspace_app.resources import make_spec

    spec = make_spec(default_user="system")
    register_skill_hub(spec)
    return spec, SkillHubStore(spec, MemoryFileStore())


async def _published(hub, body: str = "v1\n") -> str:
    from workspace_app.apps.skill_hub import SkillHubReview

    return await hub.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="inv-alice",
        source_app="rca",
        source_profile="default",
        payload={
            "SKILL.md": b"---\nname: triage\ndescription: d\n---\n\nrun scripts/x.py",
            "scripts/x.py": body.encode(),
        },
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


async def test_a_hub_copy_records_its_entry_and_reads_as_live_with_nothing_to_update():
    import msgspec

    from workspace_app.apps.skill_payload import SkillOrigin, origin_for

    _spec, hub = _hub()
    entry = await _published(hub)
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"

    await install_hub_skill(files, inv, hub, entry)

    origin = msgspec.json.decode(await files.read(inv, "/.skill/triage/.origin"), type=SkillOrigin)
    assert origin == origin_for("hub", await hub.payload_of(entry), entry=entry)
    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"v1\n"
    upstream = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert upstream is not None
    assert (upstream.state, upstream.update_available) == ("live", False)


async def test_the_owner_republishing_is_an_update_and_refresh_brings_it():
    _spec, hub = _hub()
    entry = await _published(hub)
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await install_hub_skill(files, inv, hub, entry)

    assert await _published(hub, body="v2\n") == entry
    upstream = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert upstream is not None and upstream.update_available is True

    done = await refresh_skill(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert done.updated == ["scripts/x.py"]
    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"v2\n"
    # The rewritten manifest still knows its entry: a refresh that forgot it
    # would turn the copy into an orphan that reads as `deleted` from then on.
    after = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert after is not None and (after.state, after.update_available) == ("live", False)


async def test_a_hub_copy_edited_here_is_not_an_upstream_update():
    _spec, hub = _hub()
    entry = await _published(hub)
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await install_hub_skill(files, inv, hub, entry)
    await files.write(inv, "/.skill/triage/scripts/x.py", b"the AI improved this\n")

    upstream = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert upstream is not None and upstream.update_available is False


async def test_an_unpublished_upstream_is_reported_not_raised_and_refresh_leaves_the_copy():
    """Q5: unpublish = private. The copy holder is told the state; nothing is
    pulled from an entry they may no longer read, and nothing here is touched."""
    import msgspec

    from workspace_app.apps.skill_hub import SkillHubEntry
    from workspace_app.perm import Permission

    spec, hub = _hub()
    entry = await _published(hub)
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await install_hub_skill(files, inv, hub, entry)
    rm = spec.get_resource_manager(SkillHubEntry)
    rm.update(
        entry,
        msgspec.structs.replace(rm.get(entry).data, permission=Permission(visibility="private")),
    )

    upstream = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert upstream is not None
    assert (upstream.state, upstream.update_available) == ("unpublished", False)
    nothing = await refresh_skill(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert (nothing.updated, nothing.skipped, nothing.removed) == ([], [], [])
    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"v1\n"

    # …while for its owner it is still live: private hides it from others, not from her.
    mine = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="alice")
    assert mine is not None and mine.state == "live"


async def test_a_deleted_upstream_is_reported_not_raised():
    from workspace_app.apps.skill_hub import SkillHubEntry

    spec, hub = _hub()
    entry = await _published(hub)
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await install_hub_skill(files, inv, hub, entry)
    spec.get_resource_manager(SkillHubEntry).delete(entry)

    upstream = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert upstream is not None
    assert (upstream.state, upstream.update_available) == ("deleted", False)
    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"v1\n"


async def test_a_hub_copy_with_no_hub_wired_is_a_wiring_error_not_a_quiet_state():
    """A deploy that installs from the hub always has the hub; a caller that
    forgot to pass it would otherwise read every hub copy as `deleted`."""
    _spec, hub = _hub()
    entry = await _published(hub)
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await install_hub_skill(files, inv, hub, entry)

    with pytest.raises(ValueError, match="skill hub"):
        await skill_upstream(files, inv, "rca", "local-lab", "triage")


async def test_install_writes_the_manifest_last():
    """Until `.origin` exists the copy is not a copy. A blob store that dies
    mid-install leaves files but no manifest, so the folder reads as a
    hand-written skill (no upstream, nothing to refresh) — never as a copy
    claiming shipped bytes that were never written."""
    from workspace_app.filestore.protocol import FileNotFound

    class _DiesOnSecondWrite(MemoryFileStore):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0

        async def write(self, workspace_id: str, path: str, data: bytes) -> None:
            self.writes += 1
            if self.writes == 2:
                raise OSError("disk gone")
            await super().write(workspace_id, path, data)

    _spec, hub = _hub()
    entry = await _published(hub)
    files, inv = WorkspaceFiles(_DiesOnSecondWrite()), "inv-1"

    with pytest.raises(OSError):
        await install_hub_skill(files, inv, hub, entry)

    with pytest.raises(FileNotFound):
        await files.read(inv, "/.skill/triage/.origin")
    assert (
        await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    ) is None


# ── review round 1 (Q, W) ────────────────────────────────────────────────────


async def test_has_an_update_for_a_hub_copy_reads_no_blobs():
    """The entry's row already carries the hashes of what it ships (`origin`,
    written at publish); reading every blob back to hash it again cost a
    full download per copy per panel open. Only Refresh needs the bytes."""

    class _CountsReads(MemoryFileStore):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        async def read(self, workspace_id: str, path: str) -> bytes:
            self.reads += 1
            return await super().read(workspace_id, path)

    from workspace_app.apps.skill_hub import SkillHubStore, register_skill_hub
    from workspace_app.resources import make_spec

    spec = make_spec(default_user="system")
    register_skill_hub(spec)
    blobs = _CountsReads()
    hub = SkillHubStore(spec, blobs)
    entry = await _published(hub)
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await install_hub_skill(files, inv, hub, entry)
    blobs.reads = 0

    up = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")

    assert up is not None and (up.state, up.update_available) == ("live", False)
    assert blobs.reads == 0
    assert await _published(hub, body="v2\n") == entry
    blobs.reads = 0
    up = await skill_upstream(files, inv, "rca", "local-lab", "triage", hub=hub, viewer="bob")
    assert up is not None and up.update_available is True
    assert blobs.reads == 0


async def test_install_checks_the_room_once_up_front_and_writes_nothing_when_it_does_not_fit():
    """#538's rule for a whole-folder write: a gate in the middle of the loop
    leaves half a folder without `.origin`, which then reads as a hand-written
    skill of that name — and blocks the next install as a name clash."""
    from workspace_app.files.facade import WorkspaceFull

    _spec, hub = _hub()
    entry = await _published(hub)
    payload = await hub.payload_of(entry)
    # Room for the first file alone, not for the folder: a per-write gate
    # writes SKILL.md and refuses scripts/x.py; the up-front gate writes nothing.
    quota = len(payload["SKILL.md"]) + 1
    files, inv = WorkspaceFiles(MemoryFileStore(), quota=quota), "inv-1"

    with pytest.raises(WorkspaceFull):
        await install_hub_skill(files, inv, hub, entry)

    assert await files.ls(inv, "/.skill/") == []


async def test_install_counts_the_manifest_in_its_room_check_so_a_refusal_writes_nothing():
    """Review round 2, the class swept: `install_hub_skill` checked room for the
    files and then wrote `.origin` on top — a workspace with room for the files
    and not the manifest wrote the whole folder and raised on the manifest,
    leaving exactly the half-folder the up-front check exists to prevent
    (no `.origin` → read as a hand-written skill → blocks the next install).
    The manifest is part of the operation; its bytes are in the check."""
    import msgspec

    from workspace_app.apps.skill_payload import origin_for
    from workspace_app.files import WorkspaceFull

    _spec, hub = _hub()
    entry = await _published(hub)
    payload = await hub.payload_of(entry)
    manifest = msgspec.json.encode(origin_for("hub", payload, entry=entry))
    need = sum(len(b) for b in payload.values()) + len(manifest)

    short = WorkspaceFiles(MemoryFileStore(), quota=need - 1)
    with pytest.raises(WorkspaceFull):
        await install_hub_skill(short, "inv", hub, entry)
    assert await short.ls("inv", "/") == []

    exact = WorkspaceFiles(MemoryFileStore(), quota=need)
    assert await install_hub_skill(exact, "inv", hub, entry) == "triage"
    assert await exact.exists("inv", "/.skill/triage/.origin")
