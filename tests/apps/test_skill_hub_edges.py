"""Edges review round 1 found (docs/plan-skill-hub-history.md, PR #875).

Each test names the finding it pins.
"""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest
from specstar import SpecStar

from workspace_app.apps.skill_hub import (
    SkillHubEntry,
    SkillHubReview,
    SkillHubStore,
    register_skill_hub,
    validate_skill_payload,
)
from workspace_app.apps.skill_hub_git import GitError, SkillHubRepos
from workspace_app.apps.skill_payload import ORIGIN_FILE, SkillOrigin
from workspace_app.apps.skills import SkillError, install_hub_skill, refresh_skill
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec

_MD = b"---\nname: triage\ndescription: d\n---\nbody\n"


@pytest.fixture
def spec() -> SpecStar:
    s = make_spec(default_user="alice")
    register_skill_hub(s)
    return s


@pytest.fixture
def store(spec: SpecStar, tmp_path: Path) -> SkillHubStore:
    return SkillHubStore(spec, SkillHubRepos(tmp_path / "git"))


async def _publish(store: SkillHubStore, payload: dict[str, bytes], description: str = "d") -> str:
    return await store.publish(
        owner="alice",
        name="triage",
        description=description,
        source_item="i",
        source_app="rca",
        source_profile="p",
        payload=payload,
        referenced_tools=[],
        review=SkillHubReview(verdict="ok"),
    )


async def test_a_non_owner_sees_which_version_is_current(store: SkillHubStore) -> None:
    """Defect #6: `current` was set before the owner-only rows were dropped,
    so after a visibility change no row a non-owner sees was current."""
    entry = await _publish(store, {"SKILL.md": _MD})
    await store.set_permission(entry, Permission(visibility="restricted"))
    await store.set_permission(entry, Permission(visibility="public"))

    events = await store.history(entry, viewer="bob")

    assert [(e.kind, e.current) for e in events] == [("publish", True)]


async def test_a_large_text_file_is_compared_but_not_diffed_line_by_line(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """Defect #2: a line diff of a large text file ran for seconds on the
    event loop. Past the cap it is reported changed, with no patch."""
    from workspace_app.apps.skill_hub import DIFF_TEXT_CAP

    rm = spec.get_resource_manager(SkillHubEntry)
    big = b"line\n" * (DIFF_TEXT_CAP // 5 + 1)
    entry = await _publish(store, {"SKILL.md": _MD, "data.txt": big})
    first = rm.get(entry).info.revision_id
    await _publish(store, {"SKILL.md": _MD, "data.txt": big + b"more\n"})
    second = rm.get(entry).info.revision_id

    (change,) = await store.diff(entry, first, second)

    assert (change.path, change.status, change.patch) == ("data.txt", "changed", None)


async def test_git_is_never_handed_something_that_is_not_a_commit(
    store: SkillHubStore,
) -> None:
    """Defect #9: `.origin.commit` is user-writable and reached `git ls-tree`
    as an argument — an option, for one starting with `-`."""
    entry = await _publish(store, {"SKILL.md": _MD})
    for bad in ("--output=/tmp/x", "HEAD", "master", "a" * 39):
        with pytest.raises(GitError):
            await store.repos.tree(entry, bad)


async def test_a_copy_whose_recorded_version_is_unreadable_refreshes_with_a_reason(
    store: SkillHubStore,
) -> None:
    files = WorkspaceFiles(MemoryFileStore())
    entry = await _publish(store, {"SKILL.md": _MD})
    await install_hub_skill(files, "inv", store, entry)
    origin = msgspec.json.decode(
        await files.read("inv", f"/.skill/triage/{ORIGIN_FILE}"), type=SkillOrigin
    )
    broken = msgspec.structs.replace(origin, commit="0" * 40)
    await files.write("inv", f"/.skill/triage/{ORIGIN_FILE}", msgspec.json.encode(broken))
    await _publish(store, {"SKILL.md": _MD + b"v2\n"})

    with pytest.raises(SkillError, match="reset"):
        await refresh_skill(files, "inv", "rca", "p", "triage", hub=store, viewer="bob")
    # The way out the reason names: a forced refresh needs no baseline.
    done = await refresh_skill(
        files, "inv", "rca", "p", "triage", force=True, hub=store, viewer="bob"
    )
    assert done.updated == ["SKILL.md"]


async def test_an_install_records_the_commit_of_the_files_it_wrote(
    store: SkillHubStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect #10: the row was read twice; a publish between the reads gave
    a copy whose `.origin` named one version and whose files were another's."""
    files = WorkspaceFiles(MemoryFileStore())
    entry = await _publish(store, {"SKILL.md": _MD})
    real_payload_of = store.payload_of

    async def payload_of(entry_id):  # noqa: ANN001, ANN202
        # A publish lands between the install's two reads of the row.
        await _publish(store, {"SKILL.md": _MD + b"v2\n"}, "two")
        return await real_payload_of(entry_id)

    monkeypatch.setattr(store, "payload_of", payload_of)
    await install_hub_skill(files, "inv", store, entry)

    origin = msgspec.json.decode(
        await files.read("inv", f"/.skill/triage/{ORIGIN_FILE}"), type=SkillOrigin
    )
    got = await files.read("inv", "/.skill/triage/SKILL.md")
    assert got == (await store.repos.read(entry, origin.commit))["SKILL.md"]


def test_a_skills_own_top_level_gitattributes_is_named_not_dropped() -> None:
    """Defect #7: the platform's `.gitattributes` replaced the skill's, which
    then silently never reached an install."""
    problems = validate_skill_payload("triage", {"SKILL.md": _MD, ".gitattributes": b"* text\n"})
    assert any(".gitattributes" in p for p in problems)
    nested = validate_skill_payload("triage", {"SKILL.md": _MD, "sub/.gitattributes": b"x\n"})
    assert not any(".gitattributes" in p for p in nested)


def test_only_a_real_card_declaration_is_dropped_from_tool_output() -> None:
    """Regression #5: any tool output with the marker on a line was cut there."""
    from workspace_app.agent.shown_files import SKILL_HUB_ENTRY_MARKER, without_card_declaration

    card = f'shown{SKILL_HUB_ENTRY_MARKER}{{"entry_id": "abc"}}'
    assert without_card_declaration(card) == "shown"
    grep = f"match{SKILL_HUB_ENTRY_MARKER} in a log\nnext line"
    assert without_card_declaration(grep) == grep
    not_a_card = f'{{"x": 1}}{SKILL_HUB_ENTRY_MARKER}{{"path": "a"}}'
    assert without_card_declaration(not_a_card) == not_a_card
