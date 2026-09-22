"""The backup CronJob has to agree with the code it runs.

Two of the things this manifest asserts are not style choices — they are the
correctness constraints from `docs/plan-backup.md`, and a manifest that drifts
from them produces archives that look fine and restore wrong. So they are
derived from the code's own defaults rather than restated as numbers here: a
number copied into a test is a number that stops being true silently.
"""

from __future__ import annotations

import pathlib
import re

import yaml

from workspace_app.config.schema import FilestoreSettings

_BASE = pathlib.Path(__file__).resolve().parents[2] / "kubernetes" / "base"
_CRONJOB = _BASE / "cronjob-backup.yaml"
_PVC = _BASE / "pvc.yaml"
_KUSTOMIZATION = _BASE / "kustomization.yaml"


def _docs(path: pathlib.Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _cronjob() -> dict:
    (job,) = [d for d in _docs(_CRONJOB) if d.get("kind") == "CronJob"]
    return job


def _job_spec() -> dict:
    return _cronjob()["spec"]["jobTemplate"]["spec"]


def _container() -> dict:
    (container,) = _job_spec()["template"]["spec"]["containers"]
    return container


def _seconds(duration: str) -> int:
    """`"24h"` / `"90m"` / `"30s"` as seconds — the spelling `filestore.gc_t*` uses."""
    match = re.fullmatch(r"(\d+)([hms])", duration)
    assert match, f"unhandled duration spelling {duration!r}"
    value, unit = int(match.group(1)), match.group(2)
    return value * {"h": 3600, "m": 60, "s": 1}[unit]


def test_it_runs_the_backup_entry_point():
    assert _container()["command"] == ["python", "-m", "workspace_app.backup"]


def test_a_run_cannot_outlive_the_window_blob_gc_gives_it():
    """The constraint that makes a chain trustworthy.

    A run archives records before it archives everything else. A blob orphaned
    during the run is protected from deletion for `gc_t1 + gc_t2` — after that,
    a record already written into this run's archive can reference a blob the
    collector removed before the run reached it, and the archive is quietly
    short. Bounding the run under that window makes the race impossible rather
    than unlikely.

    Derived from `FilestoreSettings` so lowering `gc_t2` fails here instead of
    silently invalidating the deadline.
    """
    defaults = FilestoreSettings()
    window = _seconds(defaults.gc_t1) + _seconds(defaults.gc_t2)

    assert _job_spec()["activeDeadlineSeconds"] < window, (
        "the backup may run longer than blob-GC's quarantine window, so a record "
        "archived early can point at a blob deleted before the run reached it"
    )


def test_two_runs_can_never_overlap():
    """Overlapping runs would write two run directories and the second would
    continue a chain the first has not finished writing — the receipt that marks
    a run complete is written last precisely so nothing reads a half-run, and two
    writers make that guarantee meaningless."""
    assert _cronjob()["spec"]["concurrencyPolicy"] == "Forbid"


def test_every_claim_it_mounts_is_at_least_written_down():
    """A manifest that mounts a claim nobody wrote down fails at apply time, in
    the middle of a deploy, for a job nobody was watching. Both this CronJob and
    its `backups` claim are opt-in, so they are checked against the file's text
    rather than its active documents — enabling one without the other is the
    mistake worth catching."""
    pvc_text = _PVC.read_text()
    mounted = {
        v["persistentVolumeClaim"]["claimName"]
        for v in _job_spec()["template"]["spec"]["volumes"]
        if "persistentVolumeClaim" in v
    }

    missing = [name for name in mounted if f"name: {name}" not in pvc_text]
    assert not missing, f"{missing} are mounted but appear nowhere in pvc.yaml"


def test_the_specstar_mount_is_not_read_only():
    """Counter-intuitive and load-bearing, so it is pinned.

    A backup "has no business writing to what it archives" is a tempting edit,
    and it breaks every run: `build_backup_spec` composes the API (so `spec.apply`
    runs the schema step) and the run records itself in the ledger — which, on a
    `filestore.kind: specstar` deployment, lives on this very volume.
    """
    data_mounts = [m for m in _container()["volumeMounts"] if m["mountPath"] == "/data"]

    assert data_mounts, "the /data mount is what the backup reads"
    assert not data_mounts[0].get("readOnly"), (
        "making /data read-only breaks the schema apply and the ledger write — "
        "see the comment in the manifest before changing this"
    )


def test_it_mounts_the_specstar_store_it_is_meant_to_archive():
    """The archive is built by reading that volume. Without the mount the run
    would still succeed — on an empty directory — which is the failure the mount
    precondition exists to catch, and no reason to rely on it here."""
    mounts = {m["mountPath"] for m in _container()["volumeMounts"]}

    assert "/data" in mounts


def test_the_cronjob_is_opt_in_and_the_kustomization_says_how():
    """Deliberately NOT in `resources:`.

    Every doc says the feature is off until `backup.dest` is set, and the code
    honours that — but applying the manifest unasked does not: the claim is
    provisioned and the job fails at 02:00 every night forever, because the run
    refuses without a destination. The `workspaces` claim beside it has been
    opt-in for the same reason since #492.

    Opt-in is only defensible if the file says so where somebody will read it,
    hence the second half of this test.
    """
    raw = _KUSTOMIZATION.read_text()
    kustomization = yaml.safe_load(raw)

    assert _CRONJOB.name not in (kustomization.get("resources") or [])
    assert f"# - {_CRONJOB.name}" in raw, "opt-in with no instructions is just missing"
    assert "BACKUP_DEST" in raw


def test_the_backups_claim_is_opt_in_too():
    """Enabling the job without its claim fails at apply time, mid-deploy. The
    two have to move together, so neither is live by default."""
    declared = {
        d["metadata"]["name"] for d in _docs(_PVC) if d.get("kind") == "PersistentVolumeClaim"
    }

    assert "backups" not in declared
    assert "backups" in _PVC.read_text(), "the claim should be present but commented out"


def test_the_staleness_threshold_is_above_what_a_slow_run_legitimately_costs():
    """An alarm that cries wolf gets muted, and then it is not an alarm.

    With `concurrencyPolicy: Forbid`, a run that uses its whole
    `activeDeadlineSeconds` pushes the next SUCCESS past the schedule interval —
    so the newest completed run can legitimately be (interval + deadline) old
    without anything being wrong. A threshold below that pages on a backup that
    is merely slow.

    Both numbers are read off the manifest and the settings rather than restated,
    so moving the schedule or the deadline fails here instead of quietly turning
    the alarm into noise.
    """
    from workspace_app.config.schema import BackupSettings

    minute, hour, *_ = _cronjob()["spec"]["schedule"].split()
    assert minute.isdigit() and hour.isdigit(), (
        "this guard assumes a fixed daily time; a different cron shape needs a "
        "different interval calculation rather than a silently wrong one"
    )
    interval_s = 24 * 3600
    deadline_s = _job_spec()["activeDeadlineSeconds"]
    threshold_s = BackupSettings().stale_after_hours * 3600

    assert threshold_s > interval_s + deadline_s, (
        f"stale_after_hours ({threshold_s // 3600}h) is not above the schedule "
        f"interval ({interval_s // 3600}h) plus the deadline ({deadline_s // 3600}h), "
        "so a slow-but-healthy run will page the operators"
    )
