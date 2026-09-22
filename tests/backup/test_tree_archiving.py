"""What the workspace tree actually contains, and what that does to a backup.

The tree is populated by the sandbox host with `rsync -rlptD`
(`sandbox-host/src/sandbox_host/nfs_archive.py:43`): `-t` preserves mtimes, `-l`
copies symlinks as symlinks, `-D` copies FIFOs and other specials. So the backup
walks a tree holding whatever a user's agent left there, with whatever
timestamps it carried.

Three defects found in adversarial review, all reproduced here first:

1. **mtime is not a change detector.** An incremental filtered by mtime drops
   every file whose mtime predates the window — which is every file a user
   unpacked from a zip or tar, restored from a backup, or copied with `cp -p`.
   Those files are in no archive at all: not the full (they did not exist yet),
   not any incremental (too old). Silent, permanent, and it scales with how much
   people unpack.
2. **`extractall(filter="data")` RAISES on inputs the backup itself produces.**
   One `ln -s /etc/passwd notes` in any workspace poisons every restore from
   then on — and it fails part-way through, leaving the tree half written.
3. **A file removed during the walk aborts the whole run.** The tar walks the
   live tree with no snapshot while agents are creating and deleting files.

The rule these share: **a backup must never fail, or silently skip, because of
what a user legitimately put in their own workspace.** Anything it cannot carry
is counted and reported, never raised and never dropped in silence.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from workspace_app.backup.tree import extract_tree, tar_tree


def _window_end() -> dt.datetime:
    """An upper bound taken NOW, not at import.

    A module-level constant looks tidier and is a trap: the cutoff would be
    fixed when the file is imported, so every file the test creates minutes
    later is "after the window" and silently filtered out. The suite caught
    this — these tests passed alone and failed in a full run.
    """
    return dt.datetime.now(dt.UTC) + dt.timedelta(seconds=5)


ANCIENT = 1_500_000_000  # 2017 — what `unzip` stamps on a member from 2017


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    (root / "item-1" / "notes").mkdir(parents=True)
    return root


def test_a_file_with_an_old_mtime_is_still_carried_by_an_incremental(tmp_path: Path):
    """The zip/tar case. `unzip` stamps members with the mtime recorded in the
    archive, so a file created seconds ago can carry a 2017 timestamp. It is NEW
    — no previous run saw it — and an incremental has to carry it."""
    root = _tree(tmp_path)
    unpacked = root / "item-1" / "from-a-zip.txt"
    unpacked.write_bytes(b"unpacked just now, stamped 2017")
    os.utime(unpacked, (ANCIENT, ANCIENT))

    result = tar_tree(root, tmp_path / "inc.tar", previous=None, window_end=_window_end())
    assert "./item-1/from-a-zip.txt" in result.paths

    # And on the next run, unchanged, it is correctly NOT carried again.
    again = tar_tree(
        root, tmp_path / "inc2.tar", previous=result.manifest, window_end=_window_end()
    )
    assert "./item-1/from-a-zip.txt" not in again.paths


def test_a_changed_file_is_carried_even_when_its_mtime_moves_backwards(tmp_path: Path):
    """Change detection cannot assume time moves forward. A file restored over
    an older one, or written by a process that preserves timestamps, changes
    content while its mtime goes DOWN."""
    root = _tree(tmp_path)
    target = root / "item-1" / "notes" / "log.txt"
    target.write_bytes(b"first")
    first = tar_tree(root, tmp_path / "a.tar", previous=None, window_end=_window_end())

    target.write_bytes(b"second, and longer than the first")
    os.utime(target, (ANCIENT, ANCIENT))

    second = tar_tree(root, tmp_path / "b.tar", previous=first.manifest, window_end=_window_end())
    assert "./item-1/notes/log.txt" in second.paths


def test_a_symlink_survives_a_round_trip(tmp_path: Path):
    """A symlink is user data. It travels, and it comes back as a symlink."""
    root = _tree(tmp_path)
    (root / "item-1" / "real.txt").write_bytes(b"target")
    (root / "item-1" / "link").symlink_to("real.txt")

    tar_tree(root, tmp_path / "t.tar", previous=None, window_end=_window_end())
    out = tmp_path / "restored"
    report = extract_tree(tmp_path / "t.tar", out)

    assert (out / "item-1" / "link").is_symlink()
    assert (out / "item-1" / "link").read_bytes() == b"target"
    assert report.skipped == ()


def test_an_absolute_symlink_is_skipped_and_counted_rather_than_raising(tmp_path: Path):
    """`ln -s /etc/passwd notes` is a thing any user can do. It must not be
    followed on extract — and it must not take the restore down with it. The
    file beside it still has to arrive."""
    root = _tree(tmp_path)
    (root / "item-1" / "keep.txt").write_bytes(b"this must survive")
    (root / "item-1" / "escape").symlink_to("/etc/passwd")

    tar_tree(root, tmp_path / "t.tar", previous=None, window_end=_window_end())
    out = tmp_path / "restored"
    report = extract_tree(tmp_path / "t.tar", out)

    assert (out / "item-1" / "keep.txt").read_bytes() == b"this must survive"
    assert any("escape" in s for s in report.skipped)
    assert not (out / "item-1" / "escape").exists()


def test_a_fifo_does_not_take_the_backup_or_the_restore_down(tmp_path: Path):
    """A FIFO has no content to back up, so it is not carried — but a workspace
    containing one must still back up and still restore."""
    root = _tree(tmp_path)
    (root / "item-1" / "data.txt").write_bytes(b"ordinary")
    os.mkfifo(root / "item-1" / "pipe")

    result = tar_tree(root, tmp_path / "t.tar", previous=None, window_end=_window_end())
    assert "./item-1/data.txt" in result.paths
    assert any("pipe" in s for s in result.skipped)

    out = tmp_path / "restored"
    extract_tree(tmp_path / "t.tar", out)
    assert (out / "item-1" / "data.txt").read_bytes() == b"ordinary"


def test_a_file_that_vanishes_mid_walk_is_counted_not_fatal(tmp_path: Path):
    """The live-tree race. The walk has no snapshot, so between listing a path
    and reading it an agent can delete it. One user's `rm` must not fail the
    night's backup for everyone."""
    root = _tree(tmp_path)
    (root / "item-1" / "stays.txt").write_bytes(b"still here")
    ghost = root / "item-1" / "gone.txt"
    ghost.write_bytes(b"about to disappear")

    def _vanish(path: Path) -> None:
        if path.name == "gone.txt":
            path.unlink()

    result = tar_tree(
        root, tmp_path / "t.tar", previous=None, window_end=_window_end(), _before_add=_vanish
    )

    assert "./item-1/stays.txt" in result.paths
    assert any("gone.txt" in s for s in result.skipped)


def test_a_deleted_file_leaves_the_manifest_so_a_later_run_does_not_resurrect_it(
    tmp_path: Path,
):
    """Not restoring deletions is a documented limitation, but the manifest must
    at least stay honest about what is there — otherwise an unchanged file that
    was deleted and recreated identically would never be carried again."""
    root = _tree(tmp_path)
    doomed = root / "item-1" / "temp.txt"
    doomed.write_bytes(b"here for now")
    first = tar_tree(root, tmp_path / "a.tar", previous=None, window_end=_window_end())
    assert "./item-1/temp.txt" in first.manifest

    doomed.unlink()
    second = tar_tree(root, tmp_path / "b.tar", previous=first.manifest, window_end=_window_end())

    assert "./item-1/temp.txt" not in second.manifest


def test_the_window_end_still_excludes_writes_that_land_after_it(tmp_path: Path):
    """The one thing mtime IS good for: an upper bound. A file written after the
    run's window closed belongs to the next run, and the drill asserts on it."""
    root = _tree(tmp_path)
    late = root / "item-1" / "late.txt"
    late.write_bytes(b"after the window closed")
    future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    os.utime(late, (future.timestamp(), future.timestamp()))

    result = tar_tree(root, tmp_path / "t.tar", previous=None, window_end=_window_end())

    assert "./item-1/late.txt" not in result.paths
