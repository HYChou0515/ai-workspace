"""P4 of `docs/plan-backup.md` — slicing, and why it is a correctness requirement
rather than a performance one.

`SpecStar.load` accumulates a model's records in memory and flushes only at
`ModelEndRecord` (specstar#450 S1), so the memory a restore needs is the size of
one model **in one archive**. Cutting a run into time windows is what bounds it:
`ModelEndRecord` arrives once per slice instead of once per dataset.

Incremental falls out of the same cut, but it is the side effect. The property
under test is that a chain restores to the same state a single full run would —
because a chain that is cheap and does not restore is worth nothing.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path

import pytest
from fastapi import FastAPI

from workspace_app.backup import chain_of, restore_chain, run_backup
from workspace_app.backup.ledger import register_backup_ledger
from workspace_app.config.schema import (
    BackupSettings,
    FilestoreSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)
from workspace_app.factories import get_spec
from workspace_app.filestore.specstar_impl import SpecstarFileStore

FIRST = b"first-payload-" + b"a" * 512
SECOND = b"second-payload-" + b"b" * 512


def _settings(
    root: Path,
    dest: Path,
    *,
    slice_days: int = 7,
    keep_chains: int = 0,
    full_every_days: int = 0,
) -> Settings:
    return Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(root)),
        sandbox=SandboxSettings(durable=SandboxDurableSettings(kind="")),
        backup=BackupSettings(
            dest=str(dest),
            require_mounted_sources=False,
            slice_days=slice_days,
            keep_chains=keep_chains,
            full_every_days=full_every_days,
        ),
    )


def _live(settings: Settings):
    """A deployment's spec, composed the way `create_app` composes one.

    Both registrations matter for a restore, and for the same reason: `load`
    refuses a model its registry does not know, so the target has to hold every
    model the source archived. `WorkspaceFile` comes from the filestore and the
    run ledger from `register_backup_ledger` — neither is in `make_spec`, which
    is exactly why the real entry points build the API's own composition.
    """
    spec = get_spec(settings)
    spec.apply(FastAPI())
    register_backup_ledger(spec)
    return spec, SpecstarFileStore(spec)


def test_a_full_plus_an_incremental_restores_what_a_single_full_would(tmp_path: Path):
    """The whole point of the chain. A restore replays the runs oldest-first, so
    both files have to be there at the end — the one the full caught and the one
    only the increment saw."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)

    asyncio.run(files.write("ws-1", "/one.txt", FIRST))
    first_run = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    asyncio.run(files.write("ws-1", "/two.txt", SECOND))
    second_run = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    assert first_run.kind == "full"
    assert second_run.kind == "incremental"
    assert second_run.chain == first_run.chain

    restored_spec, restored_files = _live(_settings(tmp_path / "restored", dest))
    for archive in chain_of(dest, second_run.chain):
        with Path(archive).open("rb") as fh:
            restored_spec.load(fh)

    assert asyncio.run(restored_files.read("ws-1", "/one.txt")) == FIRST
    assert asyncio.run(restored_files.read("ws-1", "/two.txt")) == SECOND


def test_an_incremental_archive_is_smaller_than_the_full_it_follows(tmp_path: Path):
    """The incremental must actually be narrow. Without this the chain could be
    correct and still cost a full pass every night — and the memory bound the
    slicing exists for would not hold either."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)

    for i in range(20):
        asyncio.run(files.write("ws-1", f"/bulk-{i}.txt", FIRST + bytes([i])))
    full = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    asyncio.run(files.write("ws-1", "/late.txt", SECOND))
    incremental = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    full_bytes = sum(s.bytes for s in full.source_results)
    inc_bytes = sum(s.bytes for s in incremental.source_results)
    assert inc_bytes < full_bytes


def test_forcing_a_full_starts_a_new_chain(tmp_path: Path):
    """An operator needs a way back to an independent archive — and retention
    needs chain boundaries to delete along."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))

    first = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    second = run_backup(settings, spec, now=dt.datetime.now(dt.UTC), full=True)

    assert second.kind == "full"
    assert second.chain != first.chain


def test_retention_deletes_whole_chains_never_a_link_out_of_one(tmp_path: Path):
    """The trap in retaining an incremental chain: deleting the oldest RUN would
    delete the full that every later increment builds on, leaving a directory of
    archives that cannot restore anything. Retention counts chains."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest, keep_chains=1)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))

    old_full = run_backup(settings, spec, now=dt.datetime.now(dt.UTC), full=True)
    old_inc = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    new_full = run_backup(settings, spec, now=dt.datetime.now(dt.UTC), full=True)

    assert not (dest / old_full.run_id).exists()
    assert not (dest / old_inc.run_id).exists()
    assert (dest / new_full.run_id).exists()


def test_retention_keeps_everything_by_default(tmp_path: Path):
    """`keep_chains: 0` is "delete nothing". The default has to be that: a
    retention policy that starts deleting the moment someone sets a destination is
    a policy nobody chose."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    assert settings.backup.keep_chains == 0
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))

    first = run_backup(settings, spec, now=dt.datetime.now(dt.UTC), full=True)
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC), full=True)

    assert (dest / first.run_id).exists()


def test_a_full_run_is_cut_into_windows_so_no_one_archive_holds_everything(tmp_path: Path):
    """The memory bound. With a slice narrower than the history being archived, a
    full run has to produce more than one archive per source — otherwise
    `ModelEndRecord` still arrives once for the whole dataset and `load` still
    buffers all of it."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest, slice_days=1)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))

    # A history deliberately longer than one slice.
    run = run_backup(
        settings,
        spec,
        now=dt.datetime.now(dt.UTC),
        since=dt.datetime.now(dt.UTC) - dt.timedelta(days=5),
    )

    specstar = [s for s in run.source_results if s.name == "specstar"]
    assert len(specstar) >= 5, "a 5-day history at slice_days=1 must not be one archive"


def test_an_unreadable_previous_receipt_does_not_silently_become_a_full(tmp_path: Path):
    """If the chain state cannot be read, the honest answer is to say so. Quietly
    falling back to a full would be the expensive-but-safe choice — except it also
    silently abandons the chain an operator's retention is counting."""
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))
    first = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    (dest / first.run_id / "receipt.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(ValueError, match="receipt"):
        run_backup(settings, spec, now=dt.datetime.now(dt.UTC))


def test_a_chain_older_than_full_every_days_rotates(tmp_path: Path):
    """`keep_chains` deletes along chain boundaries, so a deployment that never
    starts a new chain can never prune anything — the destination grows until it
    is full no matter what the knob says.

    ⚠️ The chain id here comes from `run_backup` itself, never from a
    hand-written string. The first version of this rotation parsed
    `chain[:16]` with `"%Y%m%dT%H%M%S"`, and a real id is exactly sixteen
    characters INCLUDING its trailing `Z` — so `strptime` raised, the `except`
    swallowed it, and the knob was dead. A test written with `"20260101T020000"`
    would have passed on that.
    """
    dest = tmp_path / "backups"
    settings = _settings(tmp_path / "data", dest, full_every_days=7, keep_chains=2)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))

    started = dt.datetime.now(dt.UTC)
    first = run_backup(settings, spec, now=started)
    soon = run_backup(settings, spec, now=started + dt.timedelta(days=1))
    assert soon.chain == first.chain, "a one-day-old chain must not rotate at 7"

    later = run_backup(settings, spec, now=started + dt.timedelta(days=30))

    assert later.kind == "full"
    assert later.chain != first.chain, (
        "a 30-day-old chain must rotate — otherwise keep_chains prunes nothing, "
        "ever, on any deployment"
    )


def test_a_coverage_change_starts_a_new_chain_rather_than_a_chain_nobody_can_restore(
    tmp_path: Path,
):
    """The day an operator turns on `nfs_tree`, the chain would otherwise hold a
    full covering {specstar} and increments covering {specstar, workspaces}.

    The restore checks coverage PER RUN (it has to — a union hides exactly this),
    so such a chain is refused with the old config AND with the new one. It is
    unrestorable with any config, forever, while the nightly backup keeps
    reporting success. The only safe answer is to start a new chain at the moment
    the set changes.
    """
    dest = tmp_path / "backups"
    tree = tmp_path / "workspaces"
    tree.mkdir()
    before = _settings(tmp_path / "data", dest)
    spec, files = _live(before)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))
    first = run_backup(before, spec, now=dt.datetime.now(dt.UTC))

    after = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(tmp_path / "data")),
        sandbox=SandboxSettings(
            durable=SandboxDurableSettings(kind="nfs_tree", nfs_root=str(tree))
        ),
        backup=BackupSettings(dest=str(dest), require_mounted_sources=False),
    )
    second = run_backup(after, spec, now=dt.datetime.now(dt.UTC))

    assert second.kind == "full"
    assert second.chain != first.chain


def test_an_incremental_reads_the_previous_runs_tree_manifest(tmp_path: Path):
    """The wiring from one run's receipt to the next run's tree diff.

    Every other run-level test in this file uses `sandbox.durable.kind: ""`, so
    until this one no test exercised a tree source through `run_backup` at all —
    `_previous_manifest`, the manifest file it reads, and the incremental tar
    path all had zero coverage. Both failure directions are silent: a full tree
    every night, or files skipped forever.
    """
    dest = tmp_path / "backups"
    tree = tmp_path / "workspaces"
    (tree / "item-1").mkdir(parents=True)
    (tree / "item-1" / "kept.md").write_bytes(b"there before the full")
    # Settled ON PURPOSE. `tar_tree` deliberately does not record a file whose
    # timestamps are too fresh to trust, so without this the assertions below
    # would hold only because `spec.apply` happens to take longer than the
    # slack — a test resting on a sleep nobody wrote.
    long_ago = (dt.datetime.now(dt.UTC) - dt.timedelta(days=30)).timestamp()
    os.utime(tree / "item-1" / "kept.md", (long_ago, long_ago))
    settings = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(tmp_path / "data")),
        sandbox=SandboxSettings(
            durable=SandboxDurableSettings(kind="nfs_tree", nfs_root=str(tree))
        ),
        backup=BackupSettings(dest=str(dest), require_mounted_sources=False),
    )
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/a.txt", FIRST))

    full = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    tree_full = next(s for s in full.source_results if s.kind == "tree")
    assert tree_full.files >= 2, "the full has to carry the tree it found"
    assert Path(tree_full.manifest).is_file(), "the run must leave a manifest to diff against"

    (tree / "item-1" / "added.md").write_bytes(b"written after the full")
    os.utime(tree / "item-1" / "added.md", (long_ago, long_ago))
    incremental = run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    tree_inc = next(s for s in incremental.source_results if s.kind == "tree")

    assert incremental.kind == "incremental"
    assert 0 < tree_inc.files < tree_full.files, (
        "the incremental must carry the new file and NOT the whole tree — "
        f"full carried {tree_full.files}, incremental carried {tree_inc.files}"
    )

    target_tree = tmp_path / "restored-workspaces"
    target = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(tmp_path / "restored")),
        sandbox=SandboxSettings(
            durable=SandboxDurableSettings(kind="nfs_tree", nfs_root=str(target_tree))
        ),
        backup=BackupSettings(dest=str(dest), require_mounted_sources=False),
    )
    target_spec, _ = _live(target)
    restore_chain(target, target_spec, confirm=True)

    assert (target_tree / "item-1" / "kept.md").read_bytes() == b"there before the full"
    assert (target_tree / "item-1" / "added.md").read_bytes() == b"written after the full"


def test_rotation_without_retention_is_warned_about(tmp_path: Path, caplog):
    """The two knobs are one policy. A new chain begins with a FULL covering all
    history, so rotating without retaining adds a complete copy of the
    deployment to the destination every period and never removes one — which
    fills the volume and then fails every night, silently at first.

    Not a refusal: an archive-everything site may mean it. But not silent."""
    dest = tmp_path / "backups"
    settings = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(tmp_path / "data")),
        sandbox=SandboxSettings(durable=SandboxDurableSettings(kind="")),
        backup=BackupSettings(
            dest=str(dest), require_mounted_sources=False, full_every_days=7, keep_chains=0
        ),
    )
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/one.txt", FIRST))

    with caplog.at_level("WARNING"):
        run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    assert any("keep_chains" in r.message for r in caplog.records)


def test_the_shipped_defaults_do_not_rotate(tmp_path: Path):
    """The control. With rotation off by default the destination holds one chain
    — one full plus increments — which is the smallest footprint and what P10
    shipped before the knob was alive."""
    assert BackupSettings().full_every_days == 0
    assert BackupSettings().keep_chains == 0
