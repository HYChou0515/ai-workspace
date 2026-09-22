"""`run_backup` — P3 of `docs/plan-backup.md`.

The behaviour under test is "what was backed up can be brought back", not "a file
of about the right size appeared". Every archive assertion below goes through a
FRESH spec that loads the archive, because a `.acbak` that exists and cannot be
loaded is the failure this whole plan is about.

Blob bytes matter specifically: specstar's dump reads each blob inside
`try: ... except Exception: pass` (#450 S2), so a blob that fails to read is
skipped and the dump still reports success. Asserting the bytes come back is the
only way to tell a complete archive from a confident one.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from workspace_app.backup import run_backup
from workspace_app.config.schema import (
    BackupSettings,
    FilestoreSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)
from workspace_app.factories import get_spec
from workspace_app.filestore.specstar_impl import SpecstarFileStore

NOW = dt.datetime(2026, 9, 23, 3, 0, tzinfo=dt.UTC)
PAYLOAD = b"THE-BYTES-THAT-MUST-SURVIVE-" + b"z" * 2048


def _settings(root: Path, dest: Path) -> Settings:
    return Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(root)),
        sandbox=SandboxSettings(durable=SandboxDurableSettings(kind="")),
        # tmp_path is not a mount point; the precondition is exercised on its own
        # in test_refuses_an_unmounted_source.
        backup=BackupSettings(dest=str(dest), require_mounted_sources=False),
    )


def _live_spec(settings: Settings):
    spec = get_spec(settings)
    spec.apply(FastAPI())
    return spec


def _write_a_file(spec, workspace: str, path: str, data: bytes) -> None:
    asyncio.run(SpecstarFileStore(spec).write(workspace, path, data))


def test_a_backup_can_be_loaded_back_into_an_empty_deployment(tmp_path: Path):
    live = tmp_path / "data"
    dest = tmp_path / "backups"
    settings = _settings(live, dest)
    spec = _live_spec(settings)
    _write_a_file(spec, "ws-1", "/notes/keep.txt", PAYLOAD)

    receipt = run_backup(settings, spec, now=NOW)

    # A brand-new deployment, nothing in it — but composed the same way, because
    # `load` refuses a model its registry does not know and `WorkspaceFile` is
    # registered by the filestore rather than by `make_spec`. That asymmetry is
    # exactly why the backup entry point builds the API's own composition.
    restored_root = tmp_path / "restored"
    restored = _live_spec(_settings(restored_root, dest))
    restored_files = SpecstarFileStore(restored)
    archive = Path(receipt.source_results[0].artifact)
    with archive.open("rb") as fh:
        restored.load(fh)

    assert asyncio.run(restored_files.read("ws-1", "/notes/keep.txt")) == PAYLOAD


def test_the_receipt_names_what_was_covered_and_why(tmp_path: Path):
    """A receipt an operator can read coverage off, rather than guess it. The
    `why` comes from `durable_sources`, so the receipt states the config that put
    each store in the run."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec = _live_spec(settings)
    _write_a_file(spec, "ws-1", "/a.txt", PAYLOAD)

    receipt = run_backup(settings, spec, now=NOW)

    on_disk = json.loads((Path(receipt.directory) / "receipt.json").read_text())
    assert on_disk["run_id"] == receipt.run_id
    assert [s["name"] for s in on_disk["sources"]] == ["specstar"]
    assert on_disk["sources"][0]["why"] == "filestore.kind: specstar"
    assert on_disk["sources"][0]["bytes"] > 0
    # A run that skipped the mount precondition must say so, or a reader cannot
    # tell a checked run from an unchecked one.
    assert on_disk["mount_checked"] is False


def test_refuses_a_deployment_with_no_durable_store(tmp_path: Path):
    """`filestore.kind: memory` persists nothing. Writing an empty archive here
    would be the worst outcome: it succeeds, and retention eventually deletes the
    last archive that held anything."""
    settings = Settings(
        filestore=FilestoreSettings(kind="memory"),
        backup=BackupSettings(dest=str(tmp_path / "backups"), require_mounted_sources=False),
    )
    spec = _live_spec(settings)

    with pytest.raises(ValueError, match="no durable store"):
        run_backup(settings, spec, now=NOW)


def test_refuses_an_unmounted_source(tmp_path: Path):
    """The failure this catches: an NFS volume that did not mount leaves an empty
    directory, the walk finds nothing, the archive is written happily, and the
    retention policy eventually deletes the archives that held real data. A path
    that is not a mount point is the exact, zero-false-positive signal for it —
    `blob-gc` legitimately shrinks counts, so a size heuristic could not be."""
    settings = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(tmp_path / "data")),
        backup=BackupSettings(dest=str(tmp_path / "backups"), require_mounted_sources=True),
    )
    spec = _live_spec(settings)

    with pytest.raises(ValueError, match="not a mount point"):
        run_backup(settings, spec, now=NOW)
