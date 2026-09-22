"""P5 of `docs/plan-backup.md`, invariant three: a run's own success is not
evidence that the archive is complete.

`ResourceManager.dump` reads each blob inside `try: ... except Exception: pass`
(specstar#450 S2) and returns a generator with no statistics, so a blob that
cannot be read is skipped and the dump finishes normally. The archive is short by
exactly the bytes somebody will want back.

Verification is a SAMPLE of referential integrity, not a count comparison.
Counts cannot work here: `blob-gc` deletes orphans on purpose, so a smaller
archive is as likely to be correct as to be broken, and any threshold that fires
on the real failure also fires after every GC pass. Referential integrity is
immune to that — a blob a live record points at is, by definition, not an orphan.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest
from fastapi import FastAPI

from workspace_app.backup import IncompleteArchive, run_backup
from workspace_app.config.schema import (
    BackupSettings,
    FilestoreSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)
from workspace_app.factories import get_spec
from workspace_app.filestore.specstar_impl import SpecstarFileStore

PAYLOAD = b"blob-bytes-that-must-be-in-the-archive-" + b"q" * 4096


def _settings(root: Path, dest: Path) -> Settings:
    return Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(root)),
        sandbox=SandboxSettings(durable=SandboxDurableSettings(kind="")),
        backup=BackupSettings(dest=str(dest), require_mounted_sources=False),
    )


def _live(settings: Settings):
    spec = get_spec(settings)
    spec.apply(FastAPI())
    return spec, SpecstarFileStore(spec)


def test_a_healthy_run_verifies(tmp_path: Path):
    settings = _settings(tmp_path / "data", tmp_path / "backups")
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/kept.txt", PAYLOAD))

    receipt = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    assert receipt.verified_blobs > 0, (
        "a run with a blob in it must have checked at least one, or the "
        "verification is reporting success on an empty sample"
    )


def test_a_blob_the_dump_silently_skipped_fails_the_run(tmp_path: Path):
    """The specstar#450 S2 failure, reproduced: remove the blob from the store and
    specstar's dump swallows the read error and finishes cleanly. The record still
    points at it, so referential integrity catches what the dump's own exit status
    could not."""
    live_root = tmp_path / "data"
    settings = _settings(live_root, tmp_path / "backups")
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/kept.txt", PAYLOAD))

    blobs = list((live_root / "_blobs").rglob("*"))
    payload_files = [p for p in blobs if p.is_file() and p.read_bytes() == PAYLOAD]
    assert payload_files, "the probe needs the blob it is about to remove"
    for path in payload_files:
        path.unlink()

    with pytest.raises(IncompleteArchive, match="blob"):
        run_backup(settings, spec, now=dt.datetime.now(dt.UTC))


def test_verification_reports_how_many_it_actually_checked(tmp_path: Path):
    """A sample of zero passes every check ever written. The count is on the
    receipt so "verified" can be told apart from "found nothing to verify"."""
    settings = _settings(tmp_path / "data", tmp_path / "backups")
    spec, files = _live(settings)
    for i in range(5):
        asyncio.run(files.write("ws-1", f"/f{i}.txt", PAYLOAD + bytes([i])))

    receipt = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    assert receipt.verified_blobs >= 5


def test_a_blob_nested_in_a_dict_field_is_sampled():
    """The walker handles dicts on purpose, and the reason is this repo's own
    blob-GC note: specstar builds a blob collector for any list / dict / union
    field whatever its value type, so "models with a Binary attribute" is not
    the real set. That branch had no test, so the generality was asserted and
    never exercised."""
    from specstar.types import Binary

    from workspace_app.backup import verify

    payload = Binary(data=b"nested", file_id="deadbeef")
    found = list(verify._binaries({"outer": [{"inner": payload}]}))

    assert [b.file_id for b in found] == ["deadbeef"]
