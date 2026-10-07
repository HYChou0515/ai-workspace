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
from workspace_app.apps.skills import install_hub_skill, refresh_skill
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

    # Looked up now, not at import: other tests `reload` the skills module,
    # after which an imported `SkillError` is a different class (round 3).
    import workspace_app.apps.skills as skills_now

    with pytest.raises(skills_now.SkillError, match="reset"):
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


async def test_the_line_diff_is_gits_and_not_quadratic(
    spec: SpecStar, store: SkillHubStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After round 3: under the 256 KiB cap, `difflib` took 17 s on a 51 KiB
    file built to be its worst case — on the shared thread pool, which any
    reader could fill. The patch comes from `git diff` (C, O(ND)), and a
    worst-case file inside the cap gets its patch."""
    import difflib

    def forbidden(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("the line diff ran in Python")

    monkeypatch.setattr(difflib, "unified_diff", forbidden)
    rm = spec.get_resource_manager(SkillHubEntry)
    n = 40_000  # ~128 KiB: inside the cap
    a = "".join(f"{i % 150}\n" for i in range(n)).encode()
    b = "".join(f"{(i * 7) % 151}\n" for i in range(n)).encode()
    entry = await _publish(store, {"SKILL.md": _MD, "data.txt": a})
    first = rm.get(entry).info.revision_id
    await _publish(store, {"SKILL.md": _MD, "data.txt": b, "new.md": b"hello\n"})
    second = rm.get(entry).info.revision_id

    changes = {c.path: c for c in await store.diff(entry, first, second)}

    patch = changes["data.txt"].patch
    assert patch is not None and patch.startswith("--- a/data.txt\n+++ b/data.txt\n@@")
    added = changes["new.md"].patch
    # The headers read as before (difflib's): a/ and b/ plus the path, even
    # for a file only one side has.
    assert added is not None and added.startswith("--- a/new.md\n+++ b/new.md\n@@")
    assert "+hello" in added


@pytest.mark.parametrize("name", ["notes with space.md", "說明.md", "-leading.md", "*.md"])
async def test_a_diff_names_any_file_the_way_it_is_called(
    spec: SpecStar, store: SkillHubStore, name: str
) -> None:
    """The path now reaches git as a pathspec: a space, CJK, a leading `-`
    or a glob character must name exactly that file."""
    rm = spec.get_resource_manager(SkillHubEntry)
    entry = await _publish(store, {"SKILL.md": _MD, name: b"one\n", "other.md": b"x\n"})
    first = rm.get(entry).info.revision_id
    await _publish(store, {"SKILL.md": _MD, name: b"two\n", "other.md": b"y\n"})
    second = rm.get(entry).info.revision_id

    changes = {c.path: c.patch for c in await store.diff(entry, first, second)}

    patch = changes[name] or ""
    # The name as it is called — not git's quoted octal for CJK, no tab.
    assert patch.startswith(f"--- a/{name}\n+++ b/{name}\n@@ ")
    assert "-one\n+two\n" in patch
    assert "-x\n" not in patch, "a glob in the name matched other files"
    assert "-x\n+y\n" in (changes["other.md"] or "")


async def test_a_hunk_header_carries_no_guessed_function_context(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """git puts the nearest line that "looks like a function" after `@@` —
    in Markdown that is any line starting with a letter. The header is the
    line numbers only, as before."""
    rm = spec.get_resource_manager(SkillHubEntry)
    body = "".join(f"Step {i}\n" for i in range(20))
    entry = await _publish(store, {"SKILL.md": _MD, "guide.md": body.encode()})
    first = rm.get(entry).info.revision_id
    changed = body.replace("Step 15", "Done").encode()
    await _publish(store, {"SKILL.md": _MD, "guide.md": changed})
    second = rm.get(entry).info.revision_id

    (change,) = [c for c in await store.diff(entry, first, second) if c.path == "guide.md"]

    hunks = [ln for ln in (change.patch or "").splitlines() if ln.startswith("@@")]
    assert hunks and all(ln.endswith("@@") for ln in hunks), hunks


async def test_the_pods_own_git_settings_do_not_reshape_a_diff(
    spec: SpecStar, store: SkillHubStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `~/.gitconfig` with `diff.context=0` or `GIT_DIFF_OPTS=--unified=0` on
    the pod used to strip the context lines from every comparison."""
    rm = spec.get_resource_manager(SkillHubEntry)
    body = "".join(f"{i}\n" for i in range(10)).encode()
    entry = await _publish(store, {"SKILL.md": _MD, "t.md": body})
    first = rm.get(entry).info.revision_id
    await _publish(store, {"SKILL.md": _MD, "t.md": body.replace(b"5\n", b"X\n")})
    second = rm.get(entry).info.revision_id
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[diff]\n\tcontext = 0\n[core]\n\tquotePath = true\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=0")

    (change,) = [c for c in await store.diff(entry, first, second) if c.path == "t.md"]

    assert change.patch is not None and " 4\n-5\n+X\n 6\n" in change.patch


async def test_one_file_git_cannot_diff_costs_that_file_only(
    spec: SpecStar, store: SkillHubStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workspace_app.apps.skill_hub_git import GitError

    rm = spec.get_resource_manager(SkillHubEntry)
    entry = await _publish(store, {"SKILL.md": _MD, "a.md": b"1\n", "b.md": b"1\n"})
    first = rm.get(entry).info.revision_id
    await _publish(store, {"SKILL.md": _MD, "a.md": b"2\n", "b.md": b"2\n"})
    second = rm.get(entry).info.revision_id
    real = store.repos.diff_text

    async def flaky(entry_id, old, new, path):  # noqa: ANN001, ANN202
        if path == "a.md":
            raise GitError("broken object")
        return await real(entry_id, old, new, path)

    monkeypatch.setattr(store.repos, "diff_text", flaky)
    changes = {c.path: c.patch for c in await store.diff(entry, first, second)}

    assert changes["a.md"] is None and "+2" in (changes["b.md"] or "")


async def test_a_file_replaced_by_a_folder_of_its_name_diffs_as_that_file_only(
    spec: SpecStar, store: SkillHubStore
) -> None:
    """A literal pathspec `foo` also matches the folder `foo/`, and git printed
    that folder's files after `foo`'s own hunks (round 4)."""
    rm = spec.get_resource_manager(SkillHubEntry)
    entry = await _publish(store, {"SKILL.md": _MD, "foo": b"x\n"})
    first = rm.get(entry).info.revision_id
    await _publish(store, {"SKILL.md": _MD, "foo/bar.md": b"y\n"})
    second = rm.get(entry).info.revision_id

    changes = {c.path: c.patch for c in await store.diff(entry, first, second)}

    assert changes["foo"] == "--- a/foo\n+++ b/foo\n@@ -1 +0,0 @@\n-x\n"
    assert changes["foo/bar.md"] == "--- a/foo/bar.md\n+++ b/foo/bar.md\n@@ -0,0 +1 @@\n+y\n"


def _git_version() -> tuple[int, ...]:
    import subprocess

    out = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    return tuple(int(x) for x in out.split()[2].split(".")[:2])


@pytest.mark.skipif(_git_version() < (2, 32), reason="GIT_CONFIG_GLOBAL arrived in git 2.32")
async def test_a_global_git_config_does_not_reach_a_diff(
    spec: SpecStar, store: SkillHubStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`color.diff = always` in the pod's `~/.gitconfig` put escape codes in
    every patch; no global config is read at all."""
    rm = spec.get_resource_manager(SkillHubEntry)
    entry = await _publish(store, {"SKILL.md": _MD, "t.md": b"1\n"})
    first = rm.get(entry).info.revision_id
    await _publish(store, {"SKILL.md": _MD, "t.md": b"2\n"})
    second = rm.get(entry).info.revision_id
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[color]\n\tdiff = always\n\tui = always\n")
    monkeypatch.setenv("HOME", str(home))

    (change,) = [c for c in await store.diff(entry, first, second) if c.path == "t.md"]

    assert change.patch == "--- a/t.md\n+++ b/t.md\n@@ -1 +1 @@\n-1\n+2\n"
