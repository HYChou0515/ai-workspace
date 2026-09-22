"""`python -m workspace_app.restore` — replay a chain of archives into this deployment.

The counterpart to `python -m workspace_app.backup`, and the half that matters on
the worst day. Same composition (`__main__.build_app`) for the same reason: `load`
refuses a model its registry does not know, and `make_spec` alone is missing
`WorkspaceFile` and the run ledger. A restore built the cheap way fails loudly on
the first archive — which is better than the alternative, but only just.

Run by hand, never on a schedule. `--confirm` is required because `load` applies
records with `on_duplicate=overwrite`: this writes over whatever is in the store
already.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backup.restore import restore_chain


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m workspace_app.restore",
        description=(
            "Replay a chain of backup archives into this deployment. Refuses when "
            "the archive and this config do not hold the same set of stores."
        ),
    )
    p.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    p.add_argument(
        "--chain",
        default=None,
        help=(
            "which chain to replay (the run id of the full run that started it). "
            "Defaults to the newest chain in backup.dest."
        ),
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "required. A restore overwrites every record it carries, so this is "
            "the difference between a recovery and an accident."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from .backup.__main__ import build_backup_spec
    from .config.loader import load_with_provenance

    args = _parse_args(argv)
    try:
        settings, _provenance = load_with_provenance(config_path=args.config)
        config_dir = args.config.parent if args.config else None
        spec = build_backup_spec(settings, config_dir=config_dir)
    except Exception as exc:
        # Under the `restore:` prefix like every other failure here — a bare
        # traceback from the composition step looks like a different kind of
        # problem than a refused restore, and at 3am that distinction costs time.
        print(f"restore: FAILED — could not compose the app: {exc}", file=sys.stderr)
        return 1
    try:
        report = restore_chain(settings, spec, confirm=args.confirm, chain=args.chain)
    except Exception as exc:
        # `print`, not a logger: an operator running this at 3am reads the
        # terminal, and logging config is not guaranteed at the point this fails.
        print(f"restore: FAILED — {exc}", file=sys.stderr)
        return 1

    print(f"restore: chain {report.chain} replayed from {len(report.runs)} run(s)")
    print(f"restore:   runs: {', '.join(report.runs)}")
    print(f"restore:   sources: {', '.join(report.sources)}")
    print(
        f"restore:   {report.archives_loaded} specstar archive(s), "
        f"{report.trees_extracted} workspace tree(s)"
    )
    print(
        "restore: NOTE the indexes travel with the archive — `load_records_bulk` "
        "saves each ResourceMeta verbatim rather than re-extracting it — so no "
        "migrate is needed for what was restored. A migrate IS still owed if the "
        "archive predates a Schema version bump this deployment has since taken: "
        "those rows come back at the old version, exactly as un-migrated rows "
        "always do. docs/migrations.md says which."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
