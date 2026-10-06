"""The packed copy of an item's archive (docs/plan-archive-pack.md).

The tree `<root>/<item>/` stays the truth; `<root>/<item>.pack.<gen>.tar` is a
cache the reopen path can read in ONE sequential NFS read instead of one round
trip per path (88,889 paths took 58 s in production). Its validity is carried
by its NAME: every writer of the tree replaces `<root>/<item>.gen` first, so a
pack made before that write is named after a gen that is no longer current and
is simply never chosen. Every failure must end as "no pack → walk the tree":
slow, never wrong.

tar runs for real (it is userland and takes milliseconds here); rsync is a
recorder, because what is pinned is which path restore took, not rsync itself.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from sandbox_host.nfs_archive import NfsArchive


class _Runner:
    """Records every argv; runs `tar` for real, answers rsync with success."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str]) -> tuple[int, bytes]:
        self.calls.append(argv)
        if Path(argv[0]).name != "tar":
            return 0, b""
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _out, err = await proc.communicate()
        return proc.returncode or 0, err

    def rsyncs(self) -> list[list[str]]:
        return [a for a in self.calls if Path(a[0]).name == "rsync"]

    def tars(self) -> list[list[str]]:
        return [a for a in self.calls if Path(a[0]).name == "tar"]


def _quiet() -> bool:
    return True


@pytest.fixture
def runner() -> _Runner:
    return _Runner()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "nfs"


@pytest.fixture
def archive(root: Path, runner: _Runner) -> NfsArchive:
    return NfsArchive(root, runner=runner)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "ws"
    (w / "sub").mkdir(parents=True)
    (w / "a.txt").write_bytes(b"alpha")
    (w / "sub" / "b.txt").write_bytes(b"beta")
    return w


def _gen(root: Path, item: str) -> str:
    return (root / f"{item}.gen").read_text().strip()


def _packs(root: Path, item: str) -> list[str]:
    return sorted(p.name for p in root.glob(f"{item}.pack.*"))


# ── every write to the tree changes the generation first ─────────────────────


@pytest.mark.parametrize("delete", [False, True])
async def test_every_persist_replaces_the_generation(
    archive: NfsArchive, root: Path, ws: Path, delete: bool
) -> None:
    await archive.persist("item-1", ws, delete=delete)
    first = _gen(root, "item-1")
    await archive.persist("item-1", ws, delete=delete)
    assert _gen(root, "item-1") != first, "a write to the tree left the generation as it was"


async def test_the_generation_changes_before_the_tree_is_written(root: Path, ws: Path) -> None:
    """Order is the whole guarantee: a pack named after the old generation must
    already be stale by the time the first byte of the new tree lands."""
    seen: list[str] = []

    async def run(argv: list[str]) -> tuple[int, bytes]:
        if Path(argv[0]).name == "rsync":
            gen = root / "item-1.gen"
            seen.append(gen.read_text().strip() if gen.exists() else "")
        return 0, b""

    archive = NfsArchive(root, runner=run)
    await archive.persist("item-1", ws, delete=False)
    first = _gen(root, "item-1")
    await archive.persist("item-1", ws, delete=False)
    assert seen[1] == _gen(root, "item-1") != first, (
        "rsync ran while the generation still named the previous tree"
    )


# ── who packs ────────────────────────────────────────────────────────────────


async def test_a_reconciling_persist_with_a_guard_leaves_a_pack_named_after_the_generation(
    archive: NfsArchive, root: Path, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (name,) = _packs(root, "item-1")
    size = (root / name).stat().st_size
    assert name == f"item-1.pack.{_gen(root, 'item-1')}-{size}.tar", (
        "the pack is not named after its generation and its size"
    )


async def test_a_pack_is_made_from_the_local_dir_in_posix_format(
    archive: NfsArchive, runner: _Runner, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (argv,) = runner.tars()
    assert "--format=posix" in argv and "-cf" in argv
    assert str(ws) in argv


async def test_an_additive_checkpoint_never_packs(
    archive: NfsArchive, root: Path, runner: _Runner, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=False, pack_guard=_quiet)
    assert _packs(root, "item-1") == [] and runner.tars() == []


async def test_without_a_guard_nothing_is_packed(
    archive: NfsArchive, root: Path, runner: _Runner, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=True)
    assert _packs(root, "item-1") == [] and runner.tars() == []


async def test_a_guard_that_fails_before_the_tar_packs_nothing(
    archive: NfsArchive, root: Path, runner: _Runner, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=True, pack_guard=lambda: False)
    assert _packs(root, "item-1") == [] and runner.tars() == []


async def test_a_guard_that_fails_after_the_tar_publishes_nothing_and_leaves_no_tmp(
    archive: NfsArchive, root: Path, runner: _Runner, ws: Path
) -> None:
    """Someone touched the sandbox while it was being packed: the tar may hold a
    file half written. Thrown away, never renamed into place."""
    answers = iter([True, False])
    await archive.persist("item-1", ws, delete=True, pack_guard=lambda: next(answers))
    assert len(runner.tars()) == 1, "the pack was never attempted — the test proves nothing"
    assert _packs(root, "item-1") == []


async def test_a_refused_reconcile_does_not_pack(
    archive: NfsArchive, root: Path, tmp_path: Path, ws: Path
) -> None:
    """An empty source over a non-empty archive is downgraded to an additive
    copy (the #492 safety valve). Packing that empty dir would publish an empty
    workspace as the fast path back."""
    await archive.persist("item-1", ws, delete=True)  # archive dir now exists…
    (root / "item-1" / "kept.txt").write_bytes(b"x")  # …and is not empty
    empty = tmp_path / "empty"
    empty.mkdir()
    await archive.persist("item-1", empty, delete=True, pack_guard=_quiet)
    assert _packs(root, "item-1") == []


async def test_packing_disabled_packs_nothing(root: Path, runner: _Runner, ws: Path) -> None:
    archive = NfsArchive(root, runner=runner, pack=False)
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    assert _packs(root, "item-1") == [] and runner.tars() == []


# ── restore ──────────────────────────────────────────────────────────────────


async def test_restore_reads_the_current_pack_instead_of_the_tree(
    archive: NfsArchive, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    runner.calls.clear()
    out = tmp_path / "out"
    assert await archive.restore("item-1", out) is True
    assert runner.rsyncs() == [], "restore walked the tree although a current pack was there"
    assert (out / "a.txt").read_bytes() == b"alpha"
    assert (out / "sub" / "b.txt").read_bytes() == b"beta"


async def test_restore_extracts_without_restoring_owners(
    archive: NfsArchive, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    """rsync never sets owners (NFS root_squash); tar run as root would, from the
    headers. `reown` re-applies the sandbox uid afterwards either way."""
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    runner.calls.clear()
    await archive.restore("item-1", tmp_path / "out")
    (argv,) = runner.tars()
    assert "-xf" in argv and "--no-same-owner" in argv


async def test_a_write_after_the_pack_makes_restore_walk_the_tree(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    await archive.persist("item-1", ws, delete=False)  # a later checkpoint
    runner.calls.clear()
    await archive.restore("item-1", tmp_path / "out")
    assert len(runner.rsyncs()) == 1 and runner.tars() == []


async def test_a_pack_named_after_another_generation_is_never_used(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    """Even if the cleanup never ran (a host that died between the generation
    change and the cleanup), the NAME decides — not the file's existence."""
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (pack,) = root.glob("item-1.pack.*.tar")
    (root / "item-1.gen").write_text("0" * 32)  # a writer moved on; pack left behind
    assert pack.exists()
    runner.calls.clear()
    await archive.restore("item-1", tmp_path / "out")
    assert len(runner.rsyncs()) == 1 and runner.tars() == []


async def test_a_file_that_only_looks_like_the_current_pack_is_never_used(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    """The lookup globs `<gen>-*.tar`; the `*` must still be a byte count."""
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (pack,) = root.glob("item-1.pack.*.tar")
    gen = _gen(root, "item-1")
    pack.rename(pack.with_name(pack.name.replace(f"{gen}-", f"{gen}-copy-")))
    runner.calls.clear()
    await archive.restore("item-1", tmp_path / "out")
    assert len(runner.rsyncs()) == 1 and runner.tars() == []


async def test_a_pack_removed_while_restore_looks_at_it_walks_the_tree(
    archive: NfsArchive,
    root: Path,
    runner: _Runner,
    tmp_path: Path,
    ws: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another pod's persist clears the old generation's pack between this
    restore's listing and its size check. That is a missing pack, not a failed
    reopen."""
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (pack,) = root.glob("item-1.pack.*.tar")
    real_stat = Path.stat

    def stat(self: Path, **kwargs):
        if self.name == pack.name:
            raise FileNotFoundError(self)
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    runner.calls.clear()
    await archive.restore("item-1", tmp_path / "out")
    assert len(runner.rsyncs()) == 1 and runner.tars() == []


async def test_a_half_written_pack_is_never_used(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=False)
    (root / "item-1.pack.tmp").write_bytes(b"not a tar")
    runner.calls.clear()
    await archive.restore("item-1", tmp_path / "out")
    assert len(runner.rsyncs()) == 1 and runner.tars() == []


def _watch_rsync(archive: NfsArchive, runner: _Runner, out: Path) -> list[list[str]]:
    """What `out` held at the moment each rsync started."""
    seen: list[list[str]] = []

    async def watching(argv: list[str]) -> tuple[int, bytes]:
        if Path(argv[0]).name == "rsync":
            seen.append(sorted(p.name for p in out.iterdir()))
        return await runner(argv)

    archive._run = watching  # type: ignore[method-assign]
    return seen


async def test_a_truncated_pack_is_never_extracted(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    """GNU tar exits 0 on an archive cut at an entry boundary: it extracts the
    first half and calls that the end. Half a workspace restored as success
    would be marked ready and the next reconciling persist would make the
    ARCHIVE match it. The byte count is in the name, so a short pack is not
    the pack the name promises."""
    for n in range(40):
        (ws / f"f{n}.txt").write_bytes(b"x" * 600)
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (pack,) = root.glob("item-1.pack.*.tar")
    data = pack.read_bytes()
    pack.write_bytes(data[: (len(data) // 2) // 512 * 512])  # at a block boundary
    out = tmp_path / "out"
    out.mkdir()
    runner.calls.clear()
    seen = _watch_rsync(archive, runner, out)

    assert await archive.restore("item-1", out) is True
    assert runner.tars() == [], "a short pack was extracted"
    assert seen == [[]]


async def test_a_pack_that_fails_mid_extract_leaves_nothing_behind_and_walks_the_tree(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    """Right size, damaged inside: tar extracts the entries before the damage
    and then fails. Half a directory must never be marked ready."""
    for n in range(40):
        (ws / f"f{n}.txt").write_bytes(b"x" * 600)
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (pack,) = root.glob("item-1.pack.*.tar")
    data = bytearray(pack.read_bytes())
    mid = (len(data) // 2) // 512 * 512
    data[mid : mid + 2048] = b"\xff" * 2048  # garbage where headers are expected
    pack.write_bytes(bytes(data))
    out = tmp_path / "out"
    out.mkdir()
    runner.calls.clear()
    seen = _watch_rsync(archive, runner, out)

    assert await archive.restore("item-1", out) is True
    assert len(runner.tars()) == 1, "the pack was never tried — the test proves nothing"
    assert seen == [[]], "the tree was copied over a half-extracted pack"


async def test_a_pack_that_fails_to_extract_is_reported(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path, ws: Path, caplog
) -> None:
    """The fallback is silent to the user — the reopen is just slow again — so
    the operator's only way to learn that packs are being made and not used is
    the host log. The host sets no log level, so it has to be a warning."""
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (pack,) = root.glob("item-1.pack.*.tar")
    pack.write_bytes(b"\xff" * pack.stat().st_size)  # right size, not a tar

    with caplog.at_level(logging.WARNING, logger="sandbox_host.nfs_archive"):
        await archive.restore("item-1", tmp_path / "out")

    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "item-1" in record.getMessage() and pack.name in record.getMessage()


async def test_restore_without_a_generation_walks_the_tree(
    archive: NfsArchive, root: Path, runner: _Runner, tmp_path: Path
) -> None:
    """An item archived before this change has a tree and no `.gen`."""
    (root / "item-1").mkdir(parents=True)
    await archive.restore("item-1", tmp_path / "out")
    assert len(runner.rsyncs()) == 1 and runner.tars() == []


async def test_packing_disabled_ignores_an_existing_pack(
    root: Path, runner: _Runner, tmp_path: Path, ws: Path
) -> None:
    await NfsArchive(root, runner=runner).persist("item-1", ws, delete=True, pack_guard=_quiet)
    runner.calls.clear()
    await NfsArchive(root, runner=runner, pack=False).restore("item-1", tmp_path / "out")
    assert len(runner.rsyncs()) == 1 and runner.tars() == []


# ── cleanup ──────────────────────────────────────────────────────────────────


async def test_a_write_removes_the_items_old_packs_and_its_tmp(
    archive: NfsArchive, root: Path, ws: Path
) -> None:
    await archive.persist("item-1", ws, delete=True, pack_guard=_quiet)
    (root / "item-1.pack.tmp").write_bytes(b"left by a host that died mid-tar")
    await archive.persist("item-1", ws, delete=False)
    assert _packs(root, "item-1") == []


async def test_cleanup_touches_only_this_items_files(
    archive: NfsArchive, root: Path, ws: Path
) -> None:
    """Item ids are free text: `a`'s cleanup must not take `a.pack.x`'s files,
    nor anything that merely starts with `a.pack.`."""
    await archive.persist("a.pack.x", ws, delete=True, pack_guard=_quiet)
    theirs = _packs(root, "a.pack.x")
    assert theirs
    (root / "a.pack.notes.txt").write_bytes(b"someone else's")
    await archive.persist("a", ws, delete=False)
    assert _packs(root, "a.pack.x") == theirs
    assert (root / "a.pack.notes.txt").exists()
