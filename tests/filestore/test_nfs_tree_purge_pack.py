"""Deleting an item takes the host's packed copy with it (docs/plan-archive-pack.md).

The host leaves two kinds of file BESIDE an item's tree, never inside it:
`<item>.gen` and `<item>.pack.<gen>-<bytes>.tar`. `purge` is the item-delete
cascade's removal of the tree; a pack it left behind is a whole workspace's
worth of bytes on NFS with no item to belong to and nothing that would ever
remove it.

The files are made by the host's own `NfsArchive`, not written by hand here:
sandbox-host ships as its own project and the root environment does not install
it, so its module is loaded from source (it imports only the standard library).
The host is the oracle for what those files are called.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

from workspace_app.filestore.nfs_tree import NfsTreeFileStore

_HOST_ARCHIVE = Path(__file__).resolve().parents[2] / "sandbox-host/src/sandbox_host/nfs_archive.py"

pytestmark = pytest.mark.skipif(shutil.which("tar") is None, reason="needs GNU tar")


def _host_archive_class():
    spec = importlib.util.spec_from_file_location("_host_nfs_archive", _HOST_ARCHIVE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.NfsArchive


async def _rsync_free(argv: list[str]) -> tuple[int, bytes]:
    """The host's runner: tar runs for real, rsync is answered (the tree's
    contents do not matter here, only the files beside it)."""
    import asyncio

    if Path(argv[0]).name != "tar":
        return 0, b""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _out, err = await proc.communicate()
    return proc.returncode or 0, err


async def _reap(root: Path, item: str, tmp_path: Path) -> None:
    ws = tmp_path / f"ws-{item}"
    ws.mkdir()
    (ws / "a.txt").write_text("alpha")
    archive = _host_archive_class()(root, runner=_rsync_free)
    await archive.persist(item, ws, delete=True, pack_guard=lambda: True)


async def test_purge_removes_the_items_pack_and_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "nfs"
    fs = NfsTreeFileStore(root)
    await fs.write("work-item:1", "/a.txt", b"alpha")
    await _reap(root, "work-item:1", tmp_path)
    beside = sorted(p.name for p in root.iterdir() if p.name != "work-item:1")
    assert any(".pack." in n for n in beside) and "work-item:1.gen" in beside, beside
    # What a host that died mid-write leaves. The generation's tmp comes from
    # the host itself, stopped before its rename; the pack's tmp is a fixed
    # name (`_pack_dir` in sandbox-host/src/sandbox_host/nfs_archive.py).
    archive = _host_archive_class()(root, runner=_rsync_free)

    def died(src, dst):
        raise SystemExit("host died before the rename")

    monkeypatch.setattr("os.replace", died)
    with pytest.raises(SystemExit):
        archive._new_generation("work-item:1")
    monkeypatch.undo()
    assert any(n.endswith(".tmp") for n in os.listdir(root)), os.listdir(root)
    (root / "work-item:1.pack.tmp").write_text("x")

    await fs.purge("work-item:1")

    assert sorted(p.name for p in root.iterdir()) == []


async def test_purge_leaves_every_other_items_files(tmp_path: Path) -> None:
    """Item ids are free text: `a`'s purge must not take `a.pack.x`'s files,
    nor a file under `a.pack.` that is nobody's pack."""
    root = tmp_path / "nfs"
    fs = NfsTreeFileStore(root)
    for item in ("a", "a.pack.x", "ab", "b"):
        await fs.write(item, "/f", b"x")
        await _reap(root, item, tmp_path)
    (root / "a.pack.notes.txt").write_text("not a pack")
    before = {p.name for p in root.iterdir()}

    await fs.purge("a")

    gone = before - {p.name for p in root.iterdir()}
    assert all(n == "a" or n == "a.gen" or n.startswith("a.pack.") for n in gone), gone
    assert "a.pack.notes.txt" not in gone
    assert not any(n.startswith(("a.pack.x", "ab", "b")) for n in gone), gone


async def test_purge_before_anything_was_ever_written_is_a_no_op(tmp_path: Path) -> None:
    await NfsTreeFileStore(tmp_path / "never-made").purge("work-item:1")
