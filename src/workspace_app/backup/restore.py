"""Bring a deployment back from a chain of archives.

P7 of `docs/plan-backup.md`. A backup nobody can restore is a directory of files
that costs money, so this is a first-class entry point rather than a runbook
paragraph — and most of it is refusals, because restoring the WRONG thing quietly
is worse than not restoring at all: the deployment then looks recovered.

Order matters. `load` applies records with `on_duplicate=overwrite`, so the chain
replays oldest-first; newest-first would leave the oldest version of every record
standing, which looks like a successful restore of stale data.

Coverage matters more. An archive made while `sandbox.durable.kind: nfs_tree` was
on holds workspace files in a tar beside the specstar archive. Loading only the
specstar half into a deployment configured the other way comes back up, serves,
and is missing every workspace file — with no error anywhere. So the archive's
own coverage is compared against what this config expects, and a mismatch stops
the restore instead of doing the part that fits.
"""

from __future__ import annotations

import json
import logging
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..config.schema import Settings
from .run import RECEIPT_NAME
from .sources import durable_sources

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

logger = logging.getLogger(__name__)


class CoverageMismatch(Exception):
    """The archive and this deployment do not hold the same set of stores.

    Raised instead of restoring the overlap. The overlap is what makes this
    dangerous: it succeeds.
    """


@dataclass(frozen=True)
class RestoreReport:
    chain: str
    runs: tuple[str, ...]
    archives_loaded: int
    trees_extracted: int
    sources: tuple[str, ...]


def restore_chain(
    settings: Settings,
    spec: SpecStar,
    *,
    confirm: bool,
    chain: str | None = None,
) -> RestoreReport:
    """Replay a chain into this deployment. `chain` defaults to the newest.

    `confirm` is not ceremony: `load` overwrites, so this writes over whatever is
    already in the store. A flag someone has to pass is the difference between a
    recovery and an accident.
    """
    if not confirm:
        raise ValueError(
            "restore refused: pass confirm=True (or --confirm). A restore loads "
            "records with on_duplicate=overwrite, so it writes over whatever is "
            "in this deployment already."
        )
    dest_setting = settings.backup.dest
    if not dest_setting:
        raise ValueError("backup.dest is unset, so there is nowhere to restore from.")
    dest = Path(dest_setting)

    receipts = _completed_runs(dest)
    if not receipts:
        raise ValueError(
            f"no completed backup was found under {dest}. A directory without a "
            "receipt.json is an interrupted run, not an archive — the receipt is "
            "written last precisely so it can mean 'this finished'."
        )
    target = chain or str(receipts[-1]["chain"])
    runs = [r for r in receipts if str(r["chain"]) == target]
    if not runs:
        raise ValueError(f"no run in {dest} belongs to chain {target!r}.")

    _check_coverage(settings, runs)
    # Where a tree goes is THIS deployment's business, not the archive's. The
    # receipt records the root it was read from, but restoring onto a different
    # mount is ordinary — a rebuilt cluster, a staging drill — and extracting to
    # the path in the receipt would write outside the deployment being restored,
    # silently, into whatever happens to be there.
    roots = {s.name: Path(s.root) for s in durable_sources(settings)}

    loaded = 0
    trees = 0
    for run in runs:  # oldest first — see the module docstring
        for source in sorted(run["sources"], key=lambda s: (s["name"] != "specstar", s["seq"])):
            artifact = Path(str(source["artifact"]))
            if not artifact.exists():
                raise ValueError(
                    f"chain {target} names {artifact}, which is not there. A chain "
                    "with a hole in it cannot be replayed: the runs after the gap "
                    "assume the records it carried."
                )
            if source["kind"] == "specstar":
                with artifact.open("rb") as fh:
                    spec.load(fh)
                loaded += 1
            else:
                _extract_tree(artifact, roots[str(source["name"])])
                trees += 1

    logger.info(
        "restore: chain %s replayed — %d archive(s), %d tree(s) across %d run(s)",
        target,
        loaded,
        trees,
        len(runs),
    )
    return RestoreReport(
        chain=target,
        runs=tuple(str(r["run_id"]) for r in runs),
        archives_loaded=loaded,
        trees_extracted=trees,
        sources=tuple(sorted({str(s["name"]) for r in runs for s in r["sources"]})),
    )


def _completed_runs(dest: Path) -> list[dict]:
    """Every finished run under `dest`, oldest first."""
    if not dest.is_dir():
        return []
    out: list[dict] = []
    for run_dir in sorted(dest.iterdir()):
        path = run_dir / RECEIPT_NAME
        if path.is_file():
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def _check_coverage(settings: Settings, runs: list[dict]) -> None:
    """Refuse when the archive and this config disagree about which stores exist.

    Compared by source NAME rather than by path: a restore onto a different mount
    point is ordinary and fine, while a restore that silently drops a whole store
    is the thing this exists to stop.
    """
    archived = {str(s["name"]) for run in runs for s in run["sources"]}
    expected = {s.name for s in durable_sources(settings)}
    if archived == expected:
        return
    missing_here = sorted(archived - expected)
    missing_there = sorted(expected - archived)
    raise CoverageMismatch(
        "this deployment and the archive do not hold the same set of stores. "
        f"In the archive but not in this config: {missing_here or 'none'}. "
        f"In this config but not in the archive: {missing_there or 'none'}. "
        "Restoring the overlap would come back up, serve, and be missing a whole "
        "store with no error anywhere — so nothing was restored. Point this "
        "deployment's config at the shape the archive was taken from, or restore "
        "a chain that matches it."
    )


def _extract_tree(artifact: Path, root: Path) -> None:
    """Unpack a workspace tar over `root`.

    `filter="data"` on purpose: a tar member can name a path outside the
    destination, and a restore is exactly the moment somebody is running this as
    root against a tree they did not produce.
    """
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(artifact, "r") as tar:
        tar.extractall(root, filter="data")
