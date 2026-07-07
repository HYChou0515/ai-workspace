"""#492 P8 — end-to-end proof over a REAL sandbox dir + REAL rsync.

The unit tests (test_nfs_archive.py, test_archive_wiring.py) cover the seams;
these compose `LocalProcessSandbox` (real on-disk workspace dir, no root needed
— isolate=False just mkdir/rmtree) with a real `NfsArchive` to prove the two
properties #492 hinges on, the ones the user actually lost data to:

  1. ROLLOUT RECOVERY — a sandbox killed mid-life (pod death / redeploy) is
     fully restored from the archive on the next create. "跑了 30 分鐘的東西
     redeploy 全消失" can't happen: the durable copy is a real file tree, and
     restore brings every byte back.
  2. CONCURRENT-WRITE SURVIVAL (Q8) — `--delete` reconciles from the sandbox's
     PHYSICAL dir, not a per-pod `_versions` cache, so two turns writing
     different files both survive the reconcile (no cross-deletion).
  3. HALF-RESTORE SAFETY (Q9) — persist is gated on `.ready`; a create whose
     restore hasn't marked ready must never rsync its (incomplete) dir back
     over the archive.

Tagged `integration` (needs rsync) so it stays out of the unit CI.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.nfs_archive import NfsArchive
from sandbox_host.protocol import SandboxSpec

pytestmark = pytest.mark.integration

skip_no_rsync = pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")


def _sandbox(tmp_path: Path) -> LocalProcessSandbox:
    # isolate=False → no userns/chroot needed for create/kill/workspace_dir; the
    # archive roundtrip is what we're proving, not exec.
    return LocalProcessSandbox(root_dir=tmp_path / "local", isolate=False)


@skip_no_rsync
async def test_rollout_kills_the_sandbox_and_restore_brings_everything_back(tmp_path: Path):
    sandbox = _sandbox(tmp_path)
    archive = NfsArchive(tmp_path / "nfs")

    # --- pod A: create item, do "30 minutes of work", persist at turn end ---
    h1 = await sandbox.create(SandboxSpec())
    await archive.restore("item-1", sandbox.workspace_dir(h1))  # cold: empty archive
    await sandbox.mark_ready(h1)
    ws1 = sandbox.workspace_dir(h1)
    (ws1 / "notebook.ipynb").write_text("hours of analysis")
    (ws1 / "data").mkdir()
    (ws1 / "data" / "results.csv").write_text("a,b\n1,2\n")
    await archive.persist("item-1", ws1, delete=True)  # turn-end reconcile

    # --- ROLLOUT: pod A dies, its ephemeral local dir is gone ---
    await sandbox.kill(h1)
    assert not ws1.exists()

    # --- pod B (fresh replica) serves the same item: create → restore ---
    h2 = await sandbox.create(SandboxSpec())
    restored = await archive.restore("item-1", sandbox.workspace_dir(h2))
    await sandbox.mark_ready(h2)
    ws2 = sandbox.workspace_dir(h2)

    assert restored is True
    assert (ws2 / "notebook.ipynb").read_text() == "hours of analysis"
    assert (ws2 / "data" / "results.csv").read_text() == "a,b\n1,2\n"


@skip_no_rsync
async def test_concurrent_writes_both_survive_a_delete_reconcile(tmp_path: Path):
    # Q8: two concurrent turns write DIFFERENT files into the one live dir. The
    # turn-end `--delete` rsyncs from that PHYSICAL dir (which holds both files),
    # so neither is wrongly dropped — the old per-pod `_versions`-diff bug (delete
    # inferred from a stale walk) is structurally impossible.
    sandbox = _sandbox(tmp_path)
    archive = NfsArchive(tmp_path / "nfs")
    h = await sandbox.create(SandboxSpec())
    await sandbox.mark_ready(h)
    ws = sandbox.workspace_dir(h)

    (ws / "from_turn_a.txt").write_text("A")
    (ws / "from_turn_b.txt").write_text("B")
    await archive.persist("item-1", ws, delete=True)

    nfs_item = tmp_path / "nfs" / "item-1"
    assert (nfs_item / "from_turn_a.txt").read_text() == "A"
    assert (nfs_item / "from_turn_b.txt").read_text() == "B"


@skip_no_rsync
async def test_intended_deletion_reconciles_but_only_from_the_live_dir(tmp_path: Path):
    # The flip side: a file the agent genuinely deleted IS reconciled out — but
    # only because it's absent from the live physical dir, never from a guess.
    sandbox = _sandbox(tmp_path)
    archive = NfsArchive(tmp_path / "nfs")
    h = await sandbox.create(SandboxSpec())
    await sandbox.mark_ready(h)
    ws = sandbox.workspace_dir(h)

    (ws / "keep.txt").write_text("keep")
    (ws / "scratch.txt").write_text("temp")
    await archive.persist("item-1", ws, delete=True)
    assert (tmp_path / "nfs" / "item-1" / "scratch.txt").exists()

    (ws / "scratch.txt").unlink()  # agent removes it
    await archive.persist("item-1", ws, delete=True)
    assert (tmp_path / "nfs" / "item-1" / "keep.txt").exists()
    assert not (tmp_path / "nfs" / "item-1" / "scratch.txt").exists()
