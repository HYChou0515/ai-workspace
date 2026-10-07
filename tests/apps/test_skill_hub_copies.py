"""Installed copies of skill hub entries on the git store
(docs/plan-skill-hub-history.md §3.3, §4.5–§4.7, G12–G17).

A copy remembers the COMMIT it was installed from; "has it changed" is that
commit against the entry's, and a refresh's three-way decision takes its
baseline from git (`ls-tree` of that commit) — so a copy carries no file map
however many files the skill has.
"""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

from workspace_app.apps.skill_hub import SkillHubReview, SkillHubStore, register_skill_hub
from workspace_app.apps.skill_hub_git import SkillHubRepos
from workspace_app.apps.skill_payload import ORIGIN_FILE, SkillOrigin, origin_for
from workspace_app.apps.skills import install_hub_skill, refresh_skill, skill_upstream
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec

_MD = b"---\nname: triage\ndescription: d\n---\nv1\n"
_PNG = b"\x89PNG" + bytes(range(256)) * 20
V1 = {"SKILL.md": _MD, "references/a.md": b"a1\n", "references/b.md": b"b1\n", "shot.png": _PNG}
INV = "inv-1"
ROOT = "/.skill/triage"


@pytest.fixture
def hub(tmp_path: Path) -> SkillHubStore:
    spec = make_spec(default_user="system")
    register_skill_hub(spec)
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"), legacy=MemoryFileStore())


@pytest.fixture
def files() -> WorkspaceFiles:
    return WorkspaceFiles(MemoryFileStore())


async def _publish(hub: SkillHubStore, payload: dict[str, bytes]) -> str:
    return await hub.publish(
        owner="alice",
        name="triage",
        description="d",
        source_item="i",
        source_app="rca",
        source_profile="default",
        payload=payload,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


async def _origin(files: WorkspaceFiles) -> SkillOrigin:
    return msgspec.json.decode(await files.read(INV, f"{ROOT}/{ORIGIN_FILE}"), type=SkillOrigin)


async def _up(files: WorkspaceFiles, hub: SkillHubStore):  # noqa: ANN202
    return await skill_upstream(files, INV, "rca", "default", "triage", hub=hub, viewer="bob")


async def _refresh(files: WorkspaceFiles, hub: SkillHubStore, *, force: bool = False):  # noqa: ANN202
    return await refresh_skill(
        files, INV, "rca", "default", "triage", force=force, hub=hub, viewer="bob"
    )


async def test_an_install_records_the_commit_and_no_file_map(
    hub: SkillHubStore, files: WorkspaceFiles
) -> None:
    """G12: the copy's `.origin` is `{source, entry, commit}`; the files are
    the version's, LFS ones as their content."""
    entry = await _publish(hub, V1)
    row = hub.get(entry)
    assert row is not None

    await install_hub_skill(files, INV, hub, entry)

    origin = await _origin(files)
    assert (origin.source, origin.entry, origin.commit, origin.files) == (
        "hub",
        entry,
        row.commit,
        {},
    )
    for rel, data in V1.items():
        assert await files.read(INV, f"{ROOT}/{rel}") == data, rel


async def test_has_it_changed_is_the_commit_and_reads_no_file(
    hub: SkillHubStore, files: WorkspaceFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G13: a Skills panel open asks this for every copy; it must not read
    the skill's files, from git or the workspace."""
    entry = await _publish(hub, V1)
    await install_hub_skill(files, INV, hub, entry)

    async def no_reads(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("'has it changed' read the files")

    monkeypatch.setattr(hub.repos, "read", no_reads)
    monkeypatch.setattr(hub.repos, "tree", no_reads)
    up = await _up(files, hub)
    assert up is not None and (up.state, up.update_available) == ("live", False)

    monkeypatch.undo()
    await _publish(hub, {**V1, "SKILL.md": _MD + b"v2\n"})
    monkeypatch.setattr(hub.repos, "read", no_reads)
    monkeypatch.setattr(hub.repos, "tree", no_reads)
    up = await _up(files, hub)
    assert up is not None and up.update_available is True


async def test_a_refresh_brings_what_upstream_changed_and_keeps_what_was_edited_here(
    hub: SkillHubStore, files: WorkspaceFiles
) -> None:
    """The three-way rule (§4.7 step 3) with git as the baseline."""
    entry = await _publish(hub, V1)
    await install_hub_skill(files, INV, hub, entry)
    await files.write(INV, f"{ROOT}/references/b.md", b"edited here\n")
    v2 = {
        "SKILL.md": _MD + b"v2\n",  # changed upstream, untouched here -> updated
        "references/b.md": b"b2\n",  # changed upstream AND here -> skipped
        "shot.png": _PNG,  # unchanged upstream -> not reported
        "references/c.md": b"c\n",  # new upstream -> updated
        # references/a.md dropped upstream, untouched here -> removed
    }
    await _publish(hub, v2)

    result = await _refresh(files, hub)

    assert (result.updated, result.skipped, result.removed) == (
        ["SKILL.md", "references/c.md"],
        ["references/b.md"],
        ["references/a.md"],
    )
    assert await files.read(INV, f"{ROOT}/SKILL.md") == v2["SKILL.md"]
    assert await files.read(INV, f"{ROOT}/references/b.md") == b"edited here\n"
    assert not await files.exists(INV, f"{ROOT}/references/a.md")
    row = hub.get(entry)
    assert row is not None and (await _origin(files)).commit == row.commit
    up = await _up(files, hub)
    assert up is not None and up.update_available is False


async def test_a_refresh_reads_from_git_only_the_files_it_writes(
    hub: SkillHubStore, files: WorkspaceFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G14: the baseline and the upstream are listed (`ls-tree`), never read;
    content is read only for a file being written into the copy."""
    entry = await _publish(hub, V1)
    await install_hub_skill(files, INV, hub, entry)
    await _publish(hub, {**V1, "references/a.md": b"a2\n"})
    asked: list[list[str] | None] = []
    real = hub.repos.read

    async def counting(entry_id: str, commit: str, *, paths=None):  # noqa: ANN001, ANN202
        asked.append(sorted(paths) if paths is not None else None)
        return await real(entry_id, commit, paths=paths)

    monkeypatch.setattr(hub.repos, "read", counting)

    result = await _refresh(files, hub)

    assert result.updated == ["references/a.md"]
    assert asked == [["references/a.md"]]


async def test_a_forced_refresh_restores_every_file(
    hub: SkillHubStore, files: WorkspaceFiles
) -> None:
    entry = await _publish(hub, V1)
    await install_hub_skill(files, INV, hub, entry)
    await files.write(INV, f"{ROOT}/references/b.md", b"edited here\n")

    result = await _refresh(files, hub, force=True)

    assert "references/b.md" in result.updated
    assert await files.read(INV, f"{ROOT}/references/b.md") == V1["references/b.md"]


async def test_a_copy_installed_before_the_git_store_is_compared_by_its_hashes(
    hub: SkillHubStore, files: WorkspaceFiles
) -> None:
    """G17: an old `.origin` has the sha256 map and no commit. It still says
    whether upstream changed — and one refresh moves it to the commit form."""
    entry = await _publish(hub, V1)
    for rel, data in V1.items():
        await files.write(INV, f"{ROOT}/{rel}", data)
    old = origin_for("hub", V1, entry=entry)
    await files.write(INV, f"{ROOT}/{ORIGIN_FILE}", msgspec.json.encode(old))

    up = await _up(files, hub)
    assert up is not None and up.update_available is False

    await files.write(INV, f"{ROOT}/references/b.md", b"edited here\n")
    await _publish(hub, {**V1, "references/a.md": b"a2\n", "references/b.md": b"b2\n"})
    up = await _up(files, hub)
    assert up is not None and up.update_available is True

    result = await _refresh(files, hub)

    assert (result.updated, result.skipped) == (["references/a.md"], ["references/b.md"])
    row = hub.get(entry)
    assert row is not None
    assert (await _origin(files)).commit == row.commit
    assert (await _origin(files)).files == {}


def test_a_manifest_written_before_commit_existed_still_decodes() -> None:
    old = b'{"source":"hub","files":{"SKILL.md":"x"},"entry":"e"}'
    assert msgspec.json.decode(old, type=SkillOrigin).commit == ""
