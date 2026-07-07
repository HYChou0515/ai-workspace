"""NfsArchive — the #492 host-side rsync bridge between a sandbox's local
working dir and the durable NFS archive.

The whole point of doing this host-side is that the bulk copy is local-disk↔NFS
and never touches the app↔host network (so it can't hang the way the old
per-file HTTP mirror did). These unit tests pin the rsync command construction
and the archive semantics with an injected runner; a real-rsync exercise lives
in the integration test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox_host.nfs_archive import NfsArchive, RsyncError


class _FakeRunner:
    """Captures the argv each call would run; returns success by default."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.rc = 0
        self.stderr = b""

    async def __call__(self, argv: list[str]) -> tuple[int, bytes]:
        self.calls.append(argv)
        return self.rc, self.stderr


@pytest.fixture
def runner() -> _FakeRunner:
    return _FakeRunner()


@pytest.fixture
def archive(tmp_path: Path, runner: _FakeRunner) -> NfsArchive:
    return NfsArchive(tmp_path / "nfs", runner=runner)


async def test_persist_builds_upload_only_rsync(
    archive: NfsArchive, runner: _FakeRunner, tmp_path: Path
):
    ws = tmp_path / "ws"
    ws.mkdir()
    await archive.persist("item-1", ws, delete=False)
    (argv,) = runner.calls
    assert argv[0] == "rsync"
    assert "-rlptD" in argv  # perms/times but NOT owner/group (root_squash-safe)
    assert "--delete" not in argv
    # trailing slashes: copy the CONTENTS of ws into the item dir
    assert argv[-2].endswith("/") and argv[-1].endswith("/")
    assert argv[-2] == f"{ws}/"


async def test_persist_with_delete_reconciles(
    archive: NfsArchive, runner: _FakeRunner, tmp_path: Path
):
    ws = tmp_path / "ws"
    ws.mkdir()
    await archive.persist("item-1", ws, delete=True)
    (argv,) = runner.calls
    assert "--delete" in argv


async def test_persist_creates_the_item_dir(archive: NfsArchive, tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    await archive.persist("item-1", ws, delete=False)
    assert (tmp_path / "nfs" / "item-1").is_dir()


async def test_persist_never_preserves_ownership(
    archive: NfsArchive, runner: _FakeRunner, tmp_path: Path
):
    ws = tmp_path / "ws"
    ws.mkdir()
    await archive.persist("item-1", ws, delete=True)
    (argv,) = runner.calls
    # -o / -g (or -a which implies them) would fail under NFS root_squash — must
    # be absent (Q3: archive is ownership-free; the host chowns on restore).
    assert "-a" not in argv
    assert "-o" not in argv
    assert "-g" not in argv


async def test_restore_builds_download_rsync_when_archive_exists(
    archive: NfsArchive, runner: _FakeRunner, tmp_path: Path
):
    (tmp_path / "nfs" / "item-1").mkdir(parents=True)
    ws = tmp_path / "ws"
    restored = await archive.restore("item-1", ws)
    assert restored is True
    (argv,) = runner.calls
    assert argv[0] == "rsync"
    assert argv[-2] == f"{tmp_path / 'nfs' / 'item-1'}/"
    assert argv[-1] == f"{ws}/"
    assert ws.is_dir()  # target created


async def test_restore_is_a_noop_when_nothing_archived(
    archive: NfsArchive, runner: _FakeRunner, tmp_path: Path
):
    """A brand-new item with no archive yet ⇒ nothing to restore, no rsync."""
    restored = await archive.restore("fresh-item", tmp_path / "ws")
    assert restored is False
    assert runner.calls == []


async def test_rsync_failure_raises(archive: NfsArchive, runner: _FakeRunner, tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner.rc = 23
    runner.stderr = b"rsync: permission denied"
    with pytest.raises(RsyncError) as ei:
        await archive.persist("item-1", ws, delete=False)
    assert "23" in str(ei.value)


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", "", "/abs"])
async def test_rejects_unsafe_item_id(archive: NfsArchive, tmp_path: Path, bad: str):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(ValueError):
        await archive.persist(bad, ws, delete=False)


async def test_default_runner_runs_real_subprocess(tmp_path: Path):
    """The unseamed archive shells out for real — cover the default runner with a
    trivially-succeeding command by pointing `rsync` at `true`."""
    ws = tmp_path / "ws"
    ws.mkdir()
    archive = NfsArchive(tmp_path / "nfs", rsync="true")
    await archive.persist("item-1", ws, delete=False)  # `true` ignores args, rc 0


async def test_default_runner_raises_on_failure(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    archive = NfsArchive(tmp_path / "nfs", rsync="false")  # always rc 1
    with pytest.raises(RsyncError):
        await archive.persist("item-1", ws, delete=False)
