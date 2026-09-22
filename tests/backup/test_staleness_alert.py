"""The other half of P5: noticing that a backup did **not** happen.

A failed run is visible — the CronJob goes red. The failure nobody sees is the
run that never started: a suspended CronJob, a cluster that lost the schedule, a
destination nobody has written to since March. Absence produces no event, so
there is nothing to alert on.

The fix is to turn absence into a row. Every completed run records itself in the
store; a sweeper reads the newest one and, when it is too old, writes a
`Notification` — which the deployment's own `INotificationChannel` then mails.
The notification is the presence that stands for the absence.

`dedup_key` carries the send-once fingerprint, because this sweeper runs on every
API pod and three pods noticing the same silence is still one thing worth saying.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

from fastapi import FastAPI
from specstar import QB

from workspace_app.backup import BackupLedger, run_backup, sweep_backup_staleness
from workspace_app.config.schema import (
    BackupSettings,
    FilestoreSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)
from workspace_app.factories import get_spec
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import Notification

SUPERUSERS = frozenset({"root", "ops"})


def _settings(root: Path, dest: Path, *, stale_after_hours: int = 26) -> Settings:
    return Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(root)),
        sandbox=SandboxSettings(durable=SandboxDurableSettings(kind="")),
        backup=BackupSettings(
            dest=str(dest),
            require_mounted_sources=False,
            stale_after_hours=stale_after_hours,
        ),
    )


def _live(settings: Settings):
    spec = get_spec(settings)
    spec.apply(FastAPI())
    return spec, SpecstarFileStore(spec)


def _alerts(spec) -> list[Notification]:
    rm = spec.get_resource_manager(Notification)
    rows = [r.data for r in rm.list_resources(QB.all().build())]
    return [n for n in rows if isinstance(n, Notification) and n.kind == "backup_stale"]


def test_a_recent_run_raises_nothing(tmp_path: Path):
    settings = _settings(tmp_path / "data", tmp_path / "backups")
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/a.txt", b"x" * 64))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))

    sent = sweep_backup_staleness(spec, settings, superusers=SUPERUSERS)

    assert sent == 0
    assert _alerts(spec) == []


def test_a_run_older_than_the_threshold_alerts_every_superuser(tmp_path: Path):
    settings = _settings(tmp_path / "data", tmp_path / "backups", stale_after_hours=1)
    spec, files = _live(settings)
    asyncio.run(files.write("ws-1", "/a.txt", b"x" * 64))
    run_backup(settings, spec, now=dt.datetime.now(dt.UTC))
    # Age the ledger row rather than the clock: the property is "the newest run
    # is old", and driving it through the row is what a real stall looks like.
    BackupLedger(spec)._age_newest_for_test(dt.timedelta(hours=5))

    sent = sweep_backup_staleness(spec, settings, superusers=SUPERUSERS)

    assert sent == len(SUPERUSERS)
    assert {n.recipient for n in _alerts(spec)} == set(SUPERUSERS)


def test_never_having_run_at_all_is_the_loudest_case(tmp_path: Path):
    """The one a "compare against the last run" check would miss entirely: there
    is no last run to compare against. A destination configured and never written
    to is a backup that has never existed."""
    settings = _settings(tmp_path / "data", tmp_path / "backups", stale_after_hours=1)
    spec, _ = _live(settings)

    sent = sweep_backup_staleness(spec, settings, superusers=SUPERUSERS)

    assert sent == len(SUPERUSERS)


def test_three_pods_noticing_the_same_silence_send_one_alert_each(tmp_path: Path):
    """The sweeper runs on every API pod. Without the send-once fingerprint an
    operator gets one mail per pod per tick, which is how an alert becomes
    something people filter out."""
    settings = _settings(tmp_path / "data", tmp_path / "backups", stale_after_hours=1)
    spec, _ = _live(settings)

    for _ in range(3):
        sweep_backup_staleness(spec, settings, superusers=SUPERUSERS)

    assert len(_alerts(spec)) == len(SUPERUSERS)


def test_an_unconfigured_deployment_is_not_nagged(tmp_path: Path):
    """`backup.dest: ""` means this deployment has not opted in. Alerting it about
    a backup it never asked for would train everyone to ignore the alert that
    matters."""
    settings = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=str(tmp_path / "data")),
        backup=BackupSettings(dest=""),
    )
    spec, _ = _live(settings)

    assert sweep_backup_staleness(spec, settings, superusers=SUPERUSERS) == 0


def test_no_superusers_means_nobody_to_tell_and_it_says_so(tmp_path: Path, caplog):
    """A silent no-op here would be the worst outcome: the deployment believes it
    is monitored and no row is ever addressed to anyone."""
    settings = _settings(tmp_path / "data", tmp_path / "backups", stale_after_hours=1)
    spec, _ = _live(settings)

    with caplog.at_level("WARNING"):
        sent = sweep_backup_staleness(spec, settings, superusers=frozenset())

    assert sent == 0
    assert any("superuser" in r.message for r in caplog.records)
