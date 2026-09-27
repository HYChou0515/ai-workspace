"""docs/plan-ai-reads-docs.md P1 — a readonly skill.

A skill whose shipped ``SKILL.md`` says ``readonly: true`` is reference material
the AI reads and never edits (the docs). So the #589 rule that protects an AI's
edits -- copy once, never overwrite -- is the wrong rule for it: its copy has to
follow what the image ships, or the AI answers from docs a deploy already
replaced. Every other skill keeps copy-if-absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import workspace_app.apps.shared_skills as shared
from workspace_app.apps.skills import readonly_skill_path, resolve_skill_body
from workspace_app.files import ReadOnlyPath, WorkspaceFiles, WorkspaceFull
from workspace_app.filestore.memory import MemoryFileStore


def _shipped(root: Path, name: str, *, readonly: bool, files: dict[str, str]) -> Path:
    sd = root / name
    sd.mkdir(parents=True, exist_ok=True)
    flag = "readonly: true\n" if readonly else ""
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n{flag}---\n\nread docs/\n")
    for rel, text in files.items():
        target = sd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return sd


@pytest.fixture
def registry(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "shipped"
    root.mkdir()
    monkeypatch.setattr(shared, "SHARED_SKILLS", {})
    return root


def _register(monkeypatch, root: Path, name: str, **kw) -> Path:
    sd = _shipped(root, name, **kw)
    monkeypatch.setitem(shared.SHARED_SKILLS, name, sd)
    return sd


async def test_a_readonly_copy_follows_what_the_image_ships(registry: Path, monkeypatch):
    sd = _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "v1"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")
    assert await files.read(inv, "/.skill/ref/docs/a.md") == b"v1"

    # a deploy ships a newer doc
    (sd / "docs" / "a.md").write_text("v2")
    await resolve_skill_body(files, inv, None, None, "ref")

    assert await files.read(inv, "/.skill/ref/docs/a.md") == b"v2"


# The comparison is what keeps a read cheap: a copy that already matches is left
# alone, not rewritten -- the docs are ~200 files, and read_skill runs every time
# the AI consults them.
async def test_a_copy_that_matches_is_not_rewritten(registry: Path, monkeypatch):
    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "v1"})
    written: list[str] = []
    files = WorkspaceFiles(MemoryFileStore(), on_write=lambda _ws, path: written.append(path))
    inv = "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")
    written.clear()

    await resolve_skill_body(files, inv, None, None, "ref")

    assert written == []


async def test_a_doc_the_image_no_longer_ships_leaves_the_copy(registry: Path, monkeypatch):
    sd = _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a", "docs/b.md": "b"}
    )
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")

    (sd / "docs" / "b.md").unlink()
    await resolve_skill_body(files, inv, None, None, "ref")

    assert await files.exists(inv, "/.skill/ref/docs/a.md")
    assert not await files.exists(inv, "/.skill/ref/docs/b.md")


# The control: an ordinary skill keeps #589's copy-if-absent, so the AI's edit
# of its copy survives a newer upstream.
async def test_an_ordinary_skill_keeps_its_copy(registry: Path, monkeypatch):
    sd = _register(monkeypatch, registry, "tool", readonly=False, files={"scripts/x.py": "v1"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, None, None, "tool")

    (sd / "scripts" / "x.py").write_text("v2")
    await resolve_skill_body(files, inv, None, None, "tool")

    assert await files.read(inv, "/.skill/tool/scripts/x.py") == b"v1"


def _guarded() -> WorkspaceFiles:
    """The facade as the app builds it: refusing writes into a readonly copy."""
    return WorkspaceFiles(MemoryFileStore(), readonly=readonly_skill_path)


async def test_a_readonly_copy_cannot_be_edited(registry: Path, monkeypatch):
    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "shipped"})
    files, inv = _guarded(), "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")

    with pytest.raises(ReadOnlyPath):
        await files.write(inv, "/.skill/ref/docs/a.md", b"edited")

    assert await files.read(inv, "/.skill/ref/docs/a.md") == b"shipped"


# Upstream decides. A copy whose own SKILL.md lost the field -- through the
# sandbox shell, which is not this facade -- is still readonly: the guard reads
# the SHIPPED SKILL.md, so deleting a line from the copy lifts nothing.
async def test_the_copy_cannot_unmark_itself(registry: Path, monkeypatch):
    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "shipped"})
    store = MemoryFileStore()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")
    await store.write(
        inv, "/.skill/ref/SKILL.md", b"---\nname: ref\ndescription: d\n---\n\nmine now\n"
    )

    with pytest.raises(ReadOnlyPath):
        await files.write(inv, "/.skill/ref/docs/a.md", b"edited")


# A skill written in the workspace has no upstream: its SKILL.md saying
# `readonly` decides nothing, and it stays the user's to edit.
async def test_a_workspace_skill_cannot_claim_readonly(registry: Path):
    files, inv = _guarded(), "inv-1"
    await files.write(
        inv, "/.skill/mine/SKILL.md", b"---\nname: mine\ndescription: d\nreadonly: true\n---\n\nx\n"
    )

    await files.write(inv, "/.skill/mine/notes.md", b"still mine")

    assert await files.read(inv, "/.skill/mine/notes.md") == b"still mine"


def _staged(tmp_path: Path) -> Path:
    src = tmp_path / "upload.bin"
    src.write_bytes(b"uploaded")
    return src


# Every door that changes a file, each refused: a guard on `write` alone leaves
# the upload, the IDE's create/edit, a move and a delete open.
DOORS = {
    "write_record": lambda f, inv, tmp: f.write_record(inv, "/.skill/ref/docs/a.md", b"x"),
    "create_exclusive": lambda f, inv, tmp: f.create_exclusive(
        inv, "/.skill/ref/docs/new.md", b"x"
    ),
    "write_from_path": lambda f, inv, tmp: f.write_from_path(
        inv, "/.skill/ref/docs/a.md", _staged(tmp)
    ),
    "create": lambda f, inv, tmp: f.create(inv, "/.skill/ref/docs/new.md", b"x"),
    "edit": lambda f, inv, tmp: f.edit(inv, "/.skill/ref/docs/a.md", "shipped", "edited"),
    "delete": lambda f, inv, tmp: f.delete(inv, "/.skill/ref/docs/a.md"),
    "move out": lambda f, inv, tmp: f.move(inv, "/.skill/ref/docs/a.md", "/a.md"),
    "move in": lambda f, inv, tmp: f.move(inv, "/mine.md", "/.skill/ref/docs/mine.md"),
    "mkdir": lambda f, inv, tmp: f.mkdir(inv, "/.skill/ref/docs/sub"),
    "rmdir": lambda f, inv, tmp: f.rmdir(inv, "/.skill/ref/docs"),
}


@pytest.mark.parametrize("door", sorted(DOORS))
async def test_every_door_into_a_readonly_copy_is_shut(registry: Path, monkeypatch, tmp_path, door):
    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "shipped"})
    files, inv = _guarded(), "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")
    await files.write(inv, "/mine.md", b"mine")

    with pytest.raises(ReadOnlyPath):
        await DOORS[door](files, inv, tmp_path)

    assert await files.read(inv, "/.skill/ref/docs/a.md") == b"shipped"
    assert not await files.exists(inv, "/.skill/ref/docs/new.md")
    assert not await files.exists(inv, "/.skill/ref/docs/mine.md")


# Review #865 round 1 (defect A): a first copy that could not finish. It used to
# stop half-written with no `.origin`: never repaired (the refresh needs the
# manifest), and never removable (the guard refuses deletes under it).
async def test_a_copy_that_does_not_fit_writes_nothing(registry: Path, monkeypatch):
    _register(
        monkeypatch,
        registry,
        "ref",
        readonly=True,
        files={"docs/a.md": "a" * 150, "docs/b.md": "b" * 150},
    )
    files = WorkspaceFiles(MemoryFileStore(), quota=200, readonly=readonly_skill_path)
    inv = "inv-1"

    with pytest.raises(WorkspaceFull):
        await resolve_skill_body(files, inv, None, None, "ref")

    assert await files.ls(inv, "/.skill/ref/") == []


class _DiesAfter(MemoryFileStore):
    """A store whose pod is killed after `left` more writes or deletes -- a
    copy interrupted part-way by the real copy code, rather than a hand-made
    imitation of what it leaves."""

    def __init__(self) -> None:
        super().__init__()
        self.left: int | None = None

    def _tick(self) -> None:
        if self.left is not None:
            if self.left == 0:
                raise RuntimeError("pod killed")
            self.left -= 1

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        self._tick()
        await super().write(workspace_id, path, data)

    async def delete(self, workspace_id: str, path: str) -> None:
        self._tick()
        await super().delete(workspace_id, path)


async def _cut_short(store: _DiesAfter, files: WorkspaceFiles, inv: str) -> None:
    store.left = 3
    with pytest.raises(RuntimeError):
        await resolve_skill_body(files, inv, None, None, "ref")
    store.left = None
    assert await files.ls(inv, "/.skill/ref/")  # it left files behind
    assert not await files.exists(inv, "/.skill/ref/.origin")  # and never finished


async def test_a_half_written_readonly_copy_is_copied_again(registry: Path, monkeypatch):
    _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a", "docs/b.md": "b"}
    )
    store = _DiesAfter()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await _cut_short(store, files, inv)

    await resolve_skill_body(files, inv, None, None, "ref")

    assert await files.read(inv, "/.skill/ref/docs/b.md") == b"b"
    assert await files.exists(inv, "/.skill/ref/.origin")


# Review #865 round 3 (defect A1, found by all four lenses): what interrupts a
# copy is most often a rollout, and the next read then runs on an image whose
# docs differ -- so "the files match what ships" cannot tell an interrupted copy
# from a person's folder. The copy says so itself, before its first file.
async def test_a_copy_cut_short_by_a_rollout_is_copied_again_by_the_new_image(
    registry: Path, monkeypatch
):
    sd = _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "v1", "docs/b.md": "b"}
    )
    store = _DiesAfter()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await _cut_short(store, files, inv)
    (sd / "docs" / "a.md").write_text("v2")  # the new image ships a different doc

    await resolve_skill_body(files, inv, None, None, "ref")

    assert await files.read(inv, "/.skill/ref/docs/a.md") == b"v2"
    assert await files.read(inv, "/.skill/ref/docs/b.md") == b"b"
    assert await files.exists(inv, "/.skill/ref/.origin")


# Review #865 round 3 (the same race, in the refresh): a refresh cut short after
# it deleted a doc upstream retired keeps the old `.origin`, which still lists
# that doc -- so every later refresh deleted it again, met nothing there, and
# that read_skill failed, for good.
async def test_a_refresh_cut_short_after_a_removal_finishes_next_time(registry: Path, monkeypatch):
    sd = _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a", "docs/b.md": "b"}
    )
    store = _DiesAfter()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")
    (sd / "docs" / "b.md").unlink()  # the new image retires b.md
    # the refresh rewrites SKILL.md and a.md, deletes b.md, and dies before `.origin`
    store.left = 3
    with pytest.raises(RuntimeError):
        await resolve_skill_body(files, inv, None, None, "ref")
    store.left = None
    assert not await files.exists(inv, "/.skill/ref/docs/b.md")

    await resolve_skill_body(files, inv, None, None, "ref")

    assert sorted(await files.ls(inv, "/.skill/ref/")) == [
        "/.skill/ref/.origin",
        "/.skill/ref/SKILL.md",
        "/.skill/ref/docs/a.md",
    ]


# Cut short in the one gap between `.origin` and the marker's removal: the copy
# is complete, and the next read drops the marker it left.
async def test_a_marker_left_beside_a_finished_copy_is_dropped(registry: Path, monkeypatch):
    _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a", "docs/b.md": "b"}
    )
    store = _DiesAfter()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    store.left = 5  # the marker, SKILL.md, a.md, b.md, `.origin` -- then the pod dies
    with pytest.raises(RuntimeError):
        await resolve_skill_body(files, inv, None, None, "ref")
    store.left = None
    assert await files.exists(inv, "/.skill/ref/.origin")
    assert await files.exists(inv, "/.skill/ref/.copying")

    await resolve_skill_body(files, inv, None, None, "ref")

    assert not await files.exists(inv, "/.skill/ref/.copying")


# The clearing can be cut short too. Its marker goes last, so what is left
# still reads as the platform's copy and the next read finishes the job.
async def test_a_clearing_cut_short_is_still_the_platforms_copy(registry: Path, monkeypatch):
    _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a", "docs/b.md": "b"}
    )
    store = _DiesAfter()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await _cut_short(store, files, inv)
    store.left = 1  # one delete of the clearing lands, then the pod dies
    with pytest.raises(RuntimeError):
        await resolve_skill_body(files, inv, None, None, "ref")
    store.left = None

    await resolve_skill_body(files, inv, None, None, "ref")

    assert await files.read(inv, "/.skill/ref/docs/b.md") == b"b"
    assert await files.exists(inv, "/.skill/ref/.origin")


# Mid-copy the marker is there; it is still not one of the skill's files, for
# whoever reads the folder as a skill (the hub, a download) or copies it on.
async def test_the_marker_is_never_one_of_the_skills_files(registry: Path, monkeypatch, tmp_path):
    from workspace_app.apps.skill_payload import skill_payload
    from workspace_app.apps.skills import workspace_skill_payload

    _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a", "docs/b.md": "b"}
    )
    store = _DiesAfter()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await _cut_short(store, files, inv)
    assert await files.exists(inv, "/.skill/ref/.copying")

    assert ".copying" not in await workspace_skill_payload(files, inv, "ref")
    src = tmp_path / "downloaded"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: x\ndescription: d\n---\n")
    (src / ".copying").write_text("")
    assert set(skill_payload(src)) == {"SKILL.md"}


# The marker is the copy's bookkeeping, like `.origin`: a finished copy does not
# keep it, and it is never one of the skill's files.
async def test_a_finished_copy_carries_no_marker(registry: Path, monkeypatch):
    from workspace_app.apps.skills import workspace_skill_payload

    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a"})
    files, inv = WorkspaceFiles(MemoryFileStore(), readonly=readonly_skill_path), "inv-1"

    await resolve_skill_body(files, inv, None, None, "ref")

    assert sorted(await files.ls(inv, "/.skill/ref/")) == [
        "/.skill/ref/.origin",
        "/.skill/ref/SKILL.md",
        "/.skill/ref/docs/a.md",
    ]
    assert set(await workspace_skill_payload(files, inv, "ref")) == {"SKILL.md", "docs/a.md"}


# Review #865 round 3 (defect B1): two reads over one interrupted copy both
# clear it; the slower one's delete met a file the other had already removed
# and raised, so that read_skill failed.
async def test_a_file_already_cleared_by_another_read_is_not_an_error(registry: Path, monkeypatch):
    _register(
        monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "a", "docs/b.md": "b"}
    )
    store = _DiesAfter()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await _cut_short(store, files, inv)
    real_ls = files.ls

    async def listed_before_the_other_read_cleared(ws: str, prefix: str = "/") -> list[str]:
        got = await real_ls(ws, prefix)
        return [*got, "/.skill/ref/docs/gone.md"] if got else got

    monkeypatch.setattr(files, "ls", listed_before_the_other_read_cleared)

    await resolve_skill_body(files, inv, None, None, "ref")

    assert await files.exists(inv, "/.skill/ref/.origin")


# Review #865 round 2 (defect A1): a folder of the person's own that carries
# the name -- written before the name was reserved -- has no `.origin` either.
# Only a folder whose every file is a shipped file, byte for byte, is an
# interrupted copy; anything else is theirs, and is never deleted.
async def test_a_persons_own_folder_with_the_name_is_never_cleared(registry: Path, monkeypatch):
    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "shipped"})
    store = MemoryFileStore()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    await store.write(
        inv, "/.skill/ref/SKILL.md", b"---\nname: ref\ndescription: mine\n---\n\nmine\n"
    )
    await store.write(inv, "/.skill/ref/notes/design.md", b"MY WORK")

    body = await resolve_skill_body(files, inv, None, None, "ref")

    assert (
        body is not None and "mine" in body
    )  # read_skill serves the folder it found (the runbook says so)
    assert await files.read(inv, "/.skill/ref/notes/design.md") == b"MY WORK"
    assert b"mine" in await files.read(inv, "/.skill/ref/SKILL.md")


# Review #865 round 2 (conformance B3): the up-front room check holds for an
# ordinary skill's copy too, not only a readonly one.
async def test_an_ordinary_copy_that_does_not_fit_writes_nothing(registry: Path, monkeypatch):
    _register(
        monkeypatch,
        registry,
        "tool",
        readonly=False,
        files={"scripts/a.py": "a" * 150, "scripts/b.py": "b" * 150},
    )
    files, inv = WorkspaceFiles(MemoryFileStore(), quota=250), "inv-1"

    with pytest.raises(WorkspaceFull):
        await resolve_skill_body(files, inv, None, None, "tool")

    assert await files.ls(inv, "/.skill/tool/") == []


# Review #865 round 2 (regression B1): a skill applied this turn whose copy does
# not fit used to stop the turn outright (only a SkillError was caught); now the
# turn goes on with a note, as for any skill that cannot load.
async def test_an_applied_skill_that_does_not_fit_is_a_note_not_a_failed_turn(
    registry: Path, monkeypatch
):
    from workspace_app.apps.skills import build_applied_skills_block

    _register(
        monkeypatch,
        registry,
        "tool",
        readonly=False,
        files={"scripts/a.py": "a" * 150, "scripts/b.py": "b" * 150},
    )
    files, inv = WorkspaceFiles(MemoryFileStore(), quota=250), "inv-1"

    block = await build_applied_skills_block(files, inv, None, None, ["tool"])

    assert "### tool" in block and "could not load" in block


# Review #865 round 3 (conformance B2): the owner's total across items refuses
# with `UserDiskFull`, which is not a `WorkspaceFull` -- the trap
# `agent/tools.py` already documents -- so it still stopped the turn.
async def test_an_applied_skill_over_the_owners_total_is_a_note_too(registry: Path, monkeypatch):
    from workspace_app.apps.skills import build_applied_skills_block
    from workspace_app.quota.disk_ledger import UserDiskFull

    _register(monkeypatch, registry, "tool", readonly=False, files={"scripts/a.py": "a"})

    async def owner_is_full(_ws: str, _after: int, extra: int, **_kw) -> None:
        raise UserDiskFull(owner="alice", used=999, quota=1000, attempted=extra)

    files, inv = WorkspaceFiles(MemoryFileStore(), person_gate=owner_is_full), "inv-1"

    block = await build_applied_skills_block(files, inv, None, None, ["tool"])

    assert "### tool" in block and "could not load" in block


# Review #865 round 1 (regression B2): a hub copy that happens to carry a
# readonly skill's name is not that skill's copy. Asking for its upstream
# without a hub raised ValueError, so read_skill crashed for the workspace.
async def test_a_hub_copy_with_a_readonly_name_is_left_alone(registry: Path, monkeypatch):
    import msgspec

    from workspace_app.apps.skill_payload import origin_for

    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "shipped"})
    store = MemoryFileStore()
    files, inv = WorkspaceFiles(store, readonly=readonly_skill_path), "inv-1"
    payload = {"SKILL.md": b"---\nname: ref\ndescription: from the hub\n---\n\nhub body\n"}
    await store.write(inv, "/.skill/ref/SKILL.md", payload["SKILL.md"])
    await store.write(
        inv, "/.skill/ref/.origin", msgspec.json.encode(origin_for("hub", payload, entry="e1"))
    )

    body = await resolve_skill_body(files, inv, None, None, "ref")

    assert body is not None and "hub body" in body


# Review #865 round 1 (defect B): the guard judged the path as written, so a
# spelling the filesystem normalises -- `/./`, `//`, `x/..` -- wrote into the copy.
@pytest.mark.parametrize(
    "path",
    [
        "/./.skill/ref/docs/a.md",
        "/.skill//ref/docs/a.md",
        "/.skill/./ref/docs/a.md",
        "/x/../.skill/ref/docs/a.md",
    ],
)
async def test_a_path_spelled_another_way_is_still_refused(registry: Path, monkeypatch, path: str):
    _register(monkeypatch, registry, "ref", readonly=True, files={"docs/a.md": "shipped"})
    files, inv = _guarded(), "inv-1"
    await resolve_skill_body(files, inv, None, None, "ref")

    with pytest.raises(ReadOnlyPath):
        await files.write(inv, path, b"edited")
