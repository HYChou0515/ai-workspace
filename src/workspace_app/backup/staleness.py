"""Turn "no backup has happened" into something an operator is told about.

The sweeper reads ONE row — the newest ledger entry — and writes a notification
when it is too old or absent. That is deliberately the whole of it: per the
lifecycle convention, a loop that runs on every API pod may only do work that is
bounded by construction, and "read the newest row" is. The pass that read every
`ClusterMember` on every pod's timer is what that convention was written after.

Delivery is not this module's business. Writing the `Notification` row IS the
send: the bell shows it immediately, and the deployment's own
`INotificationChannel` — the interface it implements, because only it knows its
relay and its from-address — mails it through the existing delivery sweeper, with
the existing retry budget. Nothing here knows what email is.

Recipients are `server.superusers`, which is already the set meaning "who runs
this deployment". A second seam for operator alerts would be a single-purpose
mechanism beside a general one that already fits.
"""

from __future__ import annotations

import datetime as dt
import logging

from ..api.notifications import notification_sent, notify
from ..config.schema import Settings
from .ledger import BackupLedger

logger = logging.getLogger(__name__)

KIND = "backup_stale"


def sweep_backup_staleness(
    spec,
    settings: Settings,
    *,
    superusers: frozenset[str],
    now: dt.datetime | None = None,
) -> int:
    """Alert the operators when the newest backup is too old. Returns rows written.

    A deployment with no `backup.dest` has not opted in and is left alone:
    nagging it about a backup it never asked for is how an alert becomes
    something people filter.
    """
    if not settings.backup.dest:
        return 0
    threshold_hours = settings.backup.stale_after_hours
    if threshold_hours <= 0:
        return 0

    stamp = now or dt.datetime.now(dt.UTC)
    newest = BackupLedger(spec).newest()
    if newest is not None:
        age = stamp - dt.datetime.fromtimestamp(newest.finished_at_ms / 1000, dt.UTC)
        if age <= dt.timedelta(hours=threshold_hours):
            return 0
        detail = (
            f"The newest backup run ({newest.run_id}, chain {newest.chain}) finished "
            f"{_hours(age)} hours ago, past the {threshold_hours}-hour threshold. "
            f"It wrote {newest.bytes} bytes to {newest.directory} and verified "
            f"{newest.verified_blobs} blob reference(s)."
        )
        window = str(int(stamp.timestamp() // (threshold_hours * 3600)))
    else:
        detail = (
            "No backup run has ever completed on this deployment, although "
            f"backup.dest is set to {settings.backup.dest!r}. Either the CronJob "
            "has never run or every run so far has failed before writing its "
            "receipt."
        )
        window = "never"

    if not superusers:
        # Loud, because the alternative is a deployment that believes it is
        # monitored while every alert is addressed to nobody.
        logger.warning(
            "backup: the newest backup is stale but server.superusers is empty, "
            "so there is no operator to notify. Set it, or nothing will ever "
            "report a backup that stopped running."
        )
        return 0

    sent = 0
    for recipient in sorted(superusers):
        # One fingerprint per operator per threshold window, so three API pods
        # noticing the same silence on the same tick say it once.
        key = f"{recipient}:{KIND}:{window}"
        if notification_sent(spec, key):
            continue
        notify(
            spec,
            recipient=recipient,
            kind=KIND,
            title="Backups have stopped",
            body=detail,
            dedup_key=key,
        )
        sent += 1
    return sent


def _hours(age: dt.timedelta) -> int:
    return int(age.total_seconds() // 3600)
