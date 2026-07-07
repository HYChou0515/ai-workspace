"""Real-rsync exercise of NfsArchive (#492).

Skipped unless `rsync` is on PATH; tagged `integration` so it stays out of the
unit CI (the seamed unit tests in test_nfs_archive.py cover the logic). Proves
the actual command persists + restores a tree and that `--delete` reconciles
removals — the property #492 depends on.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sandbox_host.nfs_archive import NfsArchive

pytestmark = pytest.mark.integration

_HAS_RSYNC = shutil.which("rsync") is not None
skip_no_rsync = pytest.mark.skipif(not _HAS_RSYNC, reason="rsync not installed")


@skip_no_rsync
async def test_persist_then_restore_roundtrips_a_tree(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "a.txt").write_bytes(b"alpha")
    (ws / "sub" / "b.txt").write_bytes(b"beta")

    archive = NfsArchive(tmp_path / "nfs")
    await archive.persist("item-1", ws, delete=True)

    out = tmp_path / "restored"
    restored = await archive.restore("item-1", out)
    assert restored is True
    assert (out / "a.txt").read_bytes() == b"alpha"
    assert (out / "sub" / "b.txt").read_bytes() == b"beta"


@skip_no_rsync
async def test_persist_with_delete_reconciles_a_removal(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "keep").write_bytes(b"k")
    (ws / "gone").write_bytes(b"g")
    archive = NfsArchive(tmp_path / "nfs")
    await archive.persist("item-1", ws, delete=True)
    assert (tmp_path / "nfs" / "item-1" / "gone").exists()

    # Remove a file locally, persist with --delete ⇒ archive drops it too.
    (ws / "gone").unlink()
    await archive.persist("item-1", ws, delete=True)
    assert (tmp_path / "nfs" / "item-1" / "keep").exists()
    assert not (tmp_path / "nfs" / "item-1" / "gone").exists()


@skip_no_rsync
async def test_upload_only_persist_does_not_delete(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f").write_bytes(b"1")
    archive = NfsArchive(tmp_path / "nfs")
    await archive.persist("item-1", ws, delete=True)
    # A mid-turn checkpoint (delete=False): a locally-removed file stays in the
    # archive (additive-only — never wrongly drops durable data).
    (ws / "f").unlink()
    await archive.persist("item-1", ws, delete=False)
    assert (tmp_path / "nfs" / "item-1" / "f").exists()
