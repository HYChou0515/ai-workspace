"""`python -m workspace_app.backup` — one backup run, then exit.

Runs as a k8s CronJob rather than a JobType. The job queue is the wrong shape for
this: the longest job this system runs once failed to converge past a 30-minute
ceiling whose cause is still not written down (`cronjob-graph.yaml`), and a full
pass here is an order of magnitude longer than that. A CronJob has
`activeDeadlineSeconds` and `concurrencyPolicy: Forbid`, which is what a long
single-shot job wants, and the process exiting hands its memory back — which
matters, because one blob is one record and the encoder copies it once more
before the write.

The spec comes from the API's OWN composition (`__main__.build_app`, built and
never served), for the same reason the `blob-gc` worker does it: `spec.dump`
archives the models in the registry, and only that composition registers all of
them. `make_spec` alone leaves out `WorkspaceFile` — it is registered by
`SpecstarFileStore.__init__` — so a backup built the cheap way would omit an
entire model and still report success. Equal by construction, not by hand.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..config.schema import Settings
from .run import run_backup

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

logger = logging.getLogger(__name__)


def build_backup_spec(settings: Settings, *, config_dir: Path | None) -> SpecStar:
    """The API's whole model registry, so an archive cannot silently omit a model.

    Deliberately `build_app` and not `get_spec`: see the module docstring. A
    parity test pins the two registries equal, because the failure of getting
    this wrong is an archive that succeeds while missing a model.
    """
    from ..__main__ import build_app

    app = build_app(settings, config_dir=config_dir)
    spec = app.state.spec
    return spec


def _aware_isoformat(text: str) -> dt.datetime:
    """An ISO-8601 stamp that definitely carries a timezone.

    `--since 2026-01-01` parses to a NAIVE datetime, which then meets an aware
    one inside the run and raises `TypeError: can't compare offset-naive and
    offset-aware datetimes` — a traceback about datetimes for what is really "you
    left the timezone off". Assume UTC and say so.
    """
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        print(f"backup: --since {text!r} has no timezone; reading it as UTC", file=sys.stderr)
        return parsed.replace(tzinfo=dt.UTC)
    return parsed


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m workspace_app.backup",
        description=(
            "Archive every durable store this config makes the app write to. "
            "Refuses rather than covering less than it claims."
        ),
    )
    p.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    p.add_argument(
        "--full",
        action="store_true",
        help=(
            "start a new chain instead of continuing the newest one. Retention "
            "deletes along chain boundaries, so this is also how an operator "
            "makes an archive that stands on its own."
        ),
    )
    p.add_argument(
        "--since",
        type=_aware_isoformat,
        default=None,
        help=(
            "ISO-8601 lower bound for this run's window, overriding the chain's. "
            "You almost certainly do not want this: a full already derives its "
            "lower bound from the oldest record and slices from there, so passing "
            "a date only moves the bound LATER and puts everything before it in no "
            "archive, permanently. It exists for re-archiving a known range."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from ..config.loader import load_with_provenance

    args = _parse_args(argv)
    try:
        settings, _provenance = load_with_provenance(config_path=args.config)
    except Exception as exc:
        print(f"backup: FAILED — config could not be loaded: {exc}", file=sys.stderr)
        return 1
    config_dir = args.config.parent if args.config else None

    # Refuse BEFORE composing the app. `build_backup_spec` builds the whole API
    # and runs `spec.apply` — the DB-schema step — against the production store.
    # A deployment that never opted into backups would otherwise do all of that
    # nightly, then refuse.
    if not settings.backup.dest:
        print(
            "backup: nothing to do — backup.dest is unset, so this deployment has "
            "not configured a backup destination. Set it in config.yaml (or drop "
            "cronjob-backup.yaml from your kustomization if you do not want one).",
            file=sys.stderr,
        )
        return 1

    try:
        spec = build_backup_spec(settings, config_dir=config_dir)
    except Exception as exc:
        # Inside the `backup:` prefix, because `kubectl logs | grep '^backup:'`
        # is what the runbook tells an operator to check. A composition failure
        # used to escape as a bare traceback with no matching line at all.
        print(f"backup: FAILED — could not compose the app: {exc}", file=sys.stderr)
        return 1
    try:
        receipt = run_backup(
            settings,
            spec,
            now=dt.datetime.now(dt.UTC),
            since=args.since,
            full=args.full,
        )
    except Exception as exc:
        # `print`, not `logger.error`: a CronJob's pod log is what an operator
        # reads when the job goes red, and logging config is not guaranteed to be
        # installed at the point this can fail.
        print(f"backup: FAILED — {exc}", file=sys.stderr)
        return 1

    print(
        f"backup: {receipt.kind} run {receipt.run_id} (chain {receipt.chain}) "
        f"-> {receipt.directory}"
    )
    print(f"backup:   window {receipt.window_start or '(everything)'} .. {receipt.window_end}")
    for source in receipt.source_results:
        detail = (
            f"models={len(source.models)}" if source.kind == "specstar" else f"files={source.files}"
        )
        print(
            f"backup:   {source.name} ({source.why}) "
            f"-> {Path(source.artifact).name} {source.bytes} bytes {detail} "
            f"in {source.duration_s}s"
        )
    if not receipt.mount_checked:
        print(
            "backup:   NOTE mount precondition was disabled (backup.require_mounted_sources: false)"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
