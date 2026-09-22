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

    result = tar_tree(
        root, tmp_path / "inc.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0
    )
    assert "./item-1/from-a-zip.txt" in result.paths

    # And on the next run, unchanged, it is correctly NOT carried again.
    again = tar_tree(
        root,
        tmp_path / "inc2.tar",
        previous=result.manifest,
        window_end=_window_end(),
        stamp_slack_ns=0,
    )
    assert "./item-1/from-a-zip.txt" not in again.paths


def test_a_changed_file_is_carried_even_when_its_mtime_moves_backwards(tmp_path: Path):
    """Change detection cannot assume time moves forward. A file restored over
    an older one, or written by a process that preserves timestamps, changes
    content while its mtime goes DOWN."""
    root = _tree(tmp_path)
    target = root / "item-1" / "notes" / "log.txt"
    target.write_bytes(b"first")
    first = tar_tree(
        root, tmp_path / "a.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0
    )

    target.write_bytes(b"second, and longer than the first")
    os.utime(target, (ANCIENT, ANCIENT))

    second = tar_tree(
        root,
        tmp_path / "b.tar",
        previous=first.manifest,
        window_end=_window_end(),
        stamp_slack_ns=0,
    )
    assert "./item-1/notes/log.txt" in second.paths


def test_a_symlink_survives_a_round_trip(tmp_path: Path):
    """A symlink is user data. It travels, and it comes back as a symlink."""
    root = _tree(tmp_path)
    (root / "item-1" / "real.txt").write_bytes(b"target")
    (root / "item-1" / "link").symlink_to("real.txt")

    tar_tree(root, tmp_path / "t.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0)
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

    tar_tree(root, tmp_path / "t.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0)
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

    result = tar_tree(
        root, tmp_path / "t.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0
    )
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
        root,
        tmp_path / "t.tar",
        previous=None,
        window_end=_window_end(),
        stamp_slack_ns=0,
        _before_add=_vanish,
    )

    assert "./item-1/stays.txt" in result.paths
    assert any("gone.txt" in s for s in result.skipped)


def test_a_deleted_file_drops_out_of_the_manifest(tmp_path: Path):
    """The manifest tracks what the chain holds of the CURRENT tree.

    ⚠️ Deliberately NOT named for preventing a resurrection, because it does
    not prevent one: a chain replay still writes back everything deleted after
    the full, since a tar has nowhere to record a deletion. What this does buy
    is that a path deleted and later recreated identically is carried again
    rather than diffed against a stale entry."""
    root = _tree(tmp_path)
    doomed = root / "item-1" / "temp.txt"
    doomed.write_bytes(b"here for now")
    first = tar_tree(
        root, tmp_path / "a.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0
    )
    assert "./item-1/temp.txt" in first.manifest

    doomed.unlink()
    second = tar_tree(
        root,
        tmp_path / "b.tar",
        previous=first.manifest,
        window_end=_window_end(),
        stamp_slack_ns=0,
    )

    assert "./item-1/temp.txt" not in second.manifest


def test_the_window_end_still_excludes_writes_that_land_after_it(tmp_path: Path):
    """The one thing mtime IS good for: an upper bound. A file written after the
    run's window closed belongs to the next run, and the drill asserts on it."""
    root = _tree(tmp_path)
    late = root / "item-1" / "late.txt"
    late.write_bytes(b"after the window closed")
    future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    os.utime(late, (future.timestamp(), future.timestamp()))

    result = tar_tree(
        root, tmp_path / "t.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0
    )

    assert "./item-1/late.txt" not in result.paths


def test_a_file_excluded_by_the_window_is_carried_by_the_NEXT_run(tmp_path: Path):
    """The manifest describes what the CHAIN holds, not what the tree looks like.

    This is the round-1 defect coming back in a new shape. A file written after
    the window closed is correctly excluded — and the first version of this code
    then recorded it in the manifest anyway, so the next run diffed against it,
    saw "unchanged", and skipped it. It was in no archive, ever, and not in
    `skipped` either.

    **Three windows, not two.** A two-run test passes on the buggy version: run 1
    excludes it, run 2 skips it, and if you only assert on run 2 you have to be
    looking for exactly this to notice. The third run is what makes the hole
    visible as permanence rather than a delay.
    """
    root = _tree(tmp_path)
    late = root / "item-1" / "late.txt"
    late.write_bytes(b"written just after the window closed")

    closed = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=30)
    first = tar_tree(root, tmp_path / "1.tar", previous=None, window_end=closed, stamp_slack_ns=0)
    assert "./item-1/late.txt" not in first.paths  # correct: outside the window

    later = _window_end()
    second = tar_tree(
        root, tmp_path / "2.tar", previous=first.manifest, window_end=later, stamp_slack_ns=0
    )
    third = tar_tree(
        root, tmp_path / "3.tar", previous=second.manifest, window_end=later, stamp_slack_ns=0
    )

    assert "./item-1/late.txt" in second.paths, (
        "the run whose window covers it must carry it — the previous run recorded "
        "it in the manifest without archiving it"
    )
    assert "./item-1/late.txt" not in third.paths  # and then it is genuinely done


def test_a_file_that_could_not_be_read_is_retried_by_the_next_run(tmp_path: Path):
    """Same invariant from the other side. A path skipped because it vanished (or
    could not be read) must not be recorded as held — the next run has to try
    again rather than diff against an entry for bytes nobody ever archived."""
    root = _tree(tmp_path)
    flaky = root / "item-1" / "flaky.txt"
    flaky.write_bytes(b"here now")

    def _vanish(path: Path) -> None:
        if path.name == "flaky.txt":
            path.unlink()

    first = tar_tree(
        root,
        tmp_path / "1.tar",
        previous=None,
        window_end=_window_end(),
        stamp_slack_ns=0,
        _before_add=_vanish,
    )
    assert any("flaky" in s for s in first.skipped)

    flaky.write_bytes(b"here now")
    second = tar_tree(
        root,
        tmp_path / "2.tar",
        previous=first.manifest,
        window_end=_window_end(),
        stamp_slack_ns=0,
    )

    assert "./item-1/flaky.txt" in second.paths


def test_a_path_whose_type_changed_restores_over_the_old_one(tmp_path: Path):
    """Replaying a chain writes over what an earlier run already put there. A
    path that was a file and became a directory (or the reverse) used to be
    skipped with a `FileExistsError`, taking its whole subtree with it."""
    root = _tree(tmp_path)
    (root / "item-1" / "thing").write_bytes(b"i was a file")
    first = tar_tree(
        root, tmp_path / "1.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0
    )

    (root / "item-1" / "thing").unlink()
    (root / "item-1" / "thing").mkdir()
    (root / "item-1" / "thing" / "inner.txt").write_bytes(b"now i am a directory")
    tar_tree(
        root,
        tmp_path / "2.tar",
        previous=first.manifest,
        window_end=_window_end(),
        stamp_slack_ns=0,
    )

    out = tmp_path / "restored"
    extract_tree(tmp_path / "1.tar", out)
    report = extract_tree(tmp_path / "2.tar", out)

    assert (out / "item-1" / "thing" / "inner.txt").read_bytes() == b"now i am a directory"
    assert report.skipped == ()


def test_a_hardlink_pointing_out_of_the_tree_is_refused(tmp_path: Path):
    """A hardlink's `linkname` is ROOT-relative in tar, not relative to the
    member's directory — so resolving it the way a symlink is resolved lets
    `../etc/passwd` through. `filter="tar"` does not check link targets at all,
    so this is the only check there is."""
    import tarfile

    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"SECRET-OUTSIDE-THE-TREE")
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tar:
        d = tarfile.TarInfo("./a")
        d.type = tarfile.DIRTYPE
        d.mode = 0o755
        tar.addfile(d)
        link = tarfile.TarInfo("./a/h")
        link.type = tarfile.LNKTYPE
        link.linkname = "../outside.txt"
        tar.addfile(link)

    out = tmp_path / "restored"
    report = extract_tree(archive, out)

    assert any("h" in s for s in report.skipped), f"the hardlink was written: {report}"
    assert not (out / "a" / "h").exists()


def test_a_file_written_while_the_walk_was_running_is_carried_again_next_run(
    tmp_path: Path,
):
    """The live-tree hole that `(size, mtime, ctime)` cannot see.

    A workspace file rewritten in the same clock granule the walk stat'd it in
    keeps all three values — and on a coarse filesystem a "granule" is a whole
    second. Measured on this machine: /tmp reports whole-second mtime AND ctime,
    so the triple genuinely does not separate them.

    So a file that fresh is archived and deliberately left OUT of the manifest.
    It costs one re-carry; the alternative costs the file.
    """
    root = _tree(tmp_path)
    fresh = root / "item-1" / "being-written.txt"
    fresh.write_bytes(b"written during the walk")

    first = tar_tree(root, tmp_path / "1.tar", previous=None, window_end=_window_end())
    assert "./item-1/being-written.txt" in first.paths
    assert "./item-1/being-written.txt" not in first.manifest, (
        "a file this fresh must not be recorded as held — the next run has to "
        "re-evaluate it rather than trust a timestamp that may not have settled"
    )

    second = tar_tree(root, tmp_path / "2.tar", previous=first.manifest, window_end=_window_end())

    assert "./item-1/being-written.txt" in second.paths


def test_the_same_fresh_file_IS_recorded_when_the_slack_is_off(tmp_path: Path):
    """The control, separated from the test above by a different mutation.

    Same file, same freshness, only the slack changes — so if the run above ever
    passes for some other reason (a bug that records nothing, say), this one
    fails. Without it, "never record anything" would satisfy both.
    """
    root = _tree(tmp_path)
    fresh = root / "item-1" / "being-written.txt"
    fresh.write_bytes(b"written during the walk")

    result = tar_tree(
        root, tmp_path / "1.tar", previous=None, window_end=_window_end(), stamp_slack_ns=0
    )

    assert "./item-1/being-written.txt" in result.paths
    assert "./item-1/being-written.txt" in result.manifest
