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


def test_every_claim_it_mounts_exists():
    """A manifest that mounts a claim nobody provisioned fails at apply time, in
    the middle of a deploy, for a job nobody was watching."""
    declared = {d["metadata"]["name"] for d in _docs(_PVC) if d.get("kind") == "PersistentVolumeClaim"}
    mounted = {
        v["persistentVolumeClaim"]["claimName"]
        for v in _job_spec()["template"]["spec"]["volumes"]
        if "persistentVolumeClaim" in v
    }

    assert mounted <= declared, f"{sorted(mounted - declared)} are mounted but never declared"


def test_it_mounts_the_specstar_store_it_is_meant_to_archive():
    """The archive is built by reading that volume. Without the mount the run
    would still succeed — on an empty directory — which is the failure the mount
    precondition exists to catch, and no reason to rely on it here."""
    mounts = {m["mountPath"] for m in _container()["volumeMounts"]}

    assert "/data" in mounts


def test_the_cronjob_is_part_of_the_base_kustomization():
    """A manifest outside `resources:` is a file nobody applies."""
    kustomization = yaml.safe_load(_KUSTOMIZATION.read_text())

    assert _CRONJOB.name in kustomization["resources"]
