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
        window = _window_start_ms(stamp, threshold_hours)
    else:
        detail = (
            "No backup run has ever completed on this deployment, although "
            f"backup.dest is set to {settings.backup.dest!r}. Either the CronJob "
            "has never run or every run so far has failed before writing its "
            "receipt."
        )
        # This window rotates too. A fixed `"never"` key would fire once per
        # operator and then be suppressed for the lifetime of the deployment —
        # so the WORST state (configured, never once succeeded) would get the
        # quietest alarm, while the merely-stale one re-alerted every window.
        window = _window_start_ms(stamp, threshold_hours)

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
        # One fingerprint per operator per threshold window, so a pod that
        # already said this in this window does not say it again.
        #
        # ⚠️ This is check-then-create, not a CAS: `notification_sent` is a plain
        # indexed query and `notify` a plain create, so three pods on the SAME
        # tick can all pass the check and all write. That is duplicate mail, not
        # lost data, and the alternative — a lease — would be more machinery than
        # the work it guards. Stated rather than claimed away.
        # The threshold is part of the key's namespace, not just its arithmetic.
        # A bare epoch start is still ambiguous across settings — a 26-hour
        # window start is also a 13-hour window start — so changing the
        # threshold would let a new alert collide with one written under the old
        # one and be silently swallowed.
        key = f"{recipient}:{KIND}:{threshold_hours}h:{window}"
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


def _window_start_ms(stamp: dt.datetime, threshold_hours: int) -> str:
    """The epoch-millisecond START of the window `stamp` falls in.

    An epoch START, never `now // interval`. The lifecycle convention says so
    and the reason bites here: dedup keys outlive the config. With a bucket
    INDEX, tightening `stale_after_hours` from 26 to 13 roughly doubles every
    index, so the new keys collide with ones written months ago — and
    `notification_sent` then silently swallows the alert. Tightening the
    threshold would make the alarm quieter, which is the opposite of what the
    operator asked for. A start timestamp cannot collide with a different
    interval's.
    """
    width_ms = threshold_hours * 3_600_000
    now_ms = int(stamp.timestamp() * 1000)
    return str(now_ms - (now_ms % width_ms))
