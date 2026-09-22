"""P7 of `docs/plan-backup.md` — the half that matters on the worst day.

A backup nobody can restore is a directory of files that costs money. So the
restore is a first-class entry point with its own refusals, and the refusals are
the interesting part: restoring the WRONG thing quietly is worse than not
restoring at all, because the deployment then looks recovered.

The manifest check is the sharp one. An archive made while
`sandbox.durable.kind: nfs_tree` was on holds workspace files in a tar beside the
specstar archive. Loading only the specstar half into a deployment configured the
other way would come back up, serve, and be missing every workspace file — with
no error anywhere.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI

from workspace_app.backup import ChainIncomplete, CoverageMismatch, run_backup
from workspace_app.backup.ledger import register_backup_ledger
from workspace_app.backup.restore import restore_chain
from workspace_app.config.schema import (
    BackupSettings,
    FilestoreSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)
from workspace_app.factories import get_spec
from workspace_app.filestore.specstar_impl import SpecstarFileStore

KEPT = b"the-bytes-a-restore-must-bring-back-" + b"k" * 1024
LATER = b"written-after-the-full-" + b"l" * 1024


def _settings(root: Path, dest: Path, *, tree: Path | None = None) -> Settings:
    durable = (
        SandboxDurableSettings(kind="nfs_tree", nfs_root=str(tree))
        if tree is not None
        else SandboxDurableSettings(kind="")
    )
    return Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(root)),
        sandbox=SandboxSettings(durable=durable),
        backup=BackupSettings(dest=str(dest), require_mounted_sources=False),
    )


def _live(settings: Settings):
    spec = get_spec(settings)
    spec.apply(FastAPI())
    register_backup_ledger(spec)
    return spec, SpecstarFileStore(spec)


def test_a_chain_restores_into_an_empty_deployment(tmp_path: Path):
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/kept.txt", KEPT))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    asyncio.run(files.write("ws-1", "/later.txt", LATER))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    target_settings = _settings(tmp_path / "restored", dest)
    target_spec, target_files = _live(target_settings)
    report = restore_chain(target_settings, target_spec, confirm=True)

    assert report.archives_loaded >= 2
    assert asyncio.run(target_files.read("ws-1", "/kept.txt")) == KEPT
    assert asyncio.run(target_files.read("ws-1", "/later.txt")) == LATER


def test_the_workspace_tree_comes_back_too(tmp_path: Path):
    """The half specstar knows nothing about. A restore that brings back the
    records and not the files leaves every workspace looking empty."""
    dest = tmp_path / "backups"
    tree = tmp_path / "workspaces"
    (tree / "item-1").mkdir(parents=True)
    (tree / "item-1" / "notes.md").write_bytes(KEPT)
    settings = _settings(tmp_path / "data", dest, tree=tree)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/a.txt", b"x" * 32))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    target_tree = tmp_path / "restored-workspaces"
    target_settings = _settings(tmp_path / "restored", dest, tree=target_tree)
    target_spec, _ = _live(target_settings)
    restore_chain(target_settings, target_spec, confirm=True)

    assert (target_tree / "item-1" / "notes.md").read_bytes() == KEPT


def test_restoring_into_a_differently_shaped_deployment_is_refused(tmp_path: Path):
    """The quiet disaster. The archive carries a workspace tree; this target is
    configured to keep workspace files in specstar instead. Loading only what
    fits would come back up and serve, missing every workspace file, with no
    error anywhere."""
    dest = tmp_path / "backups"
    tree = tmp_path / "workspaces"
    (tree / "item-1").mkdir(parents=True)
    (tree / "item-1" / "notes.md").write_bytes(KEPT)
    settings = _settings(tmp_path / "data", dest, tree=tree)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/a.txt", b"x" * 32))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    # Same destination, but a target that keeps workspaces in specstar.
    target_settings = _settings(tmp_path / "restored", dest)
    target_spec, _ = _live(target_settings)

    with pytest.raises(CoverageMismatch, match="sandbox-workspaces"):
        restore_chain(target_settings, target_spec, confirm=True)


def test_a_restore_without_confirmation_is_refused(tmp_path: Path):
    """`load` defaults to overwrite, so a restore is a destructive write over
    whatever is already there. It should take saying so."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/a.txt", KEPT))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    with pytest.raises(ValueError, match="confirm"):
        restore_chain(settings, spec, confirm=False)


def test_an_empty_destination_says_so_rather_than_succeeding_quietly(tmp_path: Path):
    """ "Restored 0 archives" reported as success is how a recovery gets declared
    done over an empty deployment."""
    settings = _settings(tmp_path / "data", tmp_path / "backups")
    spec, _ = _live(settings)

    with pytest.raises(ValueError, match="no completed backup"):
        restore_chain(settings, spec, confirm=True)


def test_a_chain_whose_full_is_missing_is_refused(tmp_path: Path):
    """A deleted run directory takes its receipt with it, so the run is simply
    absent from the list — invisible to an "is every named artifact there?"
    check. If the missing one is the full, the increments replay and the restore
    reports success while every record older than the first surviving window
    stays lost."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", KEPT))
    full = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    asyncio.run(files.write("ws-1", "/two.txt", LATER))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    shutil.rmtree(dest / full.run_id)

    target_spec, _ = _live(_settings(tmp_path / "restored", dest))
    with pytest.raises(ChainIncomplete, match="does not start with a full run"):
        restore_chain(_settings(tmp_path / "restored", dest), target_spec, confirm=True)


def test_a_chain_with_a_hole_in_the_middle_is_refused(tmp_path: Path):
    """Same class, harder to see: the full survives and an increment in the
    middle does not, so every run after the gap assumes records nobody has."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", KEPT))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    asyncio.run(files.write("ws-1", "/two.txt", LATER))
    middle = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    asyncio.run(files.write("ws-1", "/three.txt", KEPT + b"3"))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    shutil.rmtree(dest / middle.run_id)

    target_spec, _ = _live(_settings(tmp_path / "restored", dest))
    with pytest.raises(ChainIncomplete, match="has a hole"):
        restore_chain(_settings(tmp_path / "restored", dest), target_spec, confirm=True)


def test_an_archive_carrying_an_unknown_model_is_refused_before_anything_is_written(
    tmp_path: Path,
):
    """`spec.load` raises on an unknown model MID-STREAM, after every model that
    sorted earlier has already been flushed — no transaction, no record of how
    far it got. The receipt names the models, so this is answerable first.

    The real-world shape is an archive from a newer image restored onto a
    rolled-back one."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", KEPT))
    receipt = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    # Forge what a newer image's archive would look like from this one's.
    path = Path(receipt.directory) / "receipt.json"
    raw = json.loads(path.read_text())
    raw["sources"][0]["models"] = [*raw["sources"][0]["models"], "a-model-from-the-future"]
    path.write_text(json.dumps(raw))

    target_spec, _ = _live(_settings(tmp_path / "restored", dest))
    with pytest.raises(CoverageMismatch, match="a-model-from-the-future"):
        restore_chain(_settings(tmp_path / "restored", dest), target_spec, confirm=True)
