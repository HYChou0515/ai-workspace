"""Perform one backup run.

P3 of `docs/plan-backup.md`. The specstar store travels as specstar's own
`.acbak` archive — blob bytes inline, so the archive is self-contained and
nothing here needs to understand the on-disk layout (the sharded directories, the
revision symlinks, the U+2215 in a path). The sandbox's workspace tree travels as
a tar, because it is an ordinary file tree and nothing else claims to own it.

Written straight into the destination rather than staged locally first: at this
size a staging copy would need as much free disk as the archive, on a pod.

What this deliberately does NOT do is go over HTTP. specstar's own
`/_backup/export` buffers the whole archive into a `BytesIO` before it answers
(#450 S5), and `api/app.py` fences that door shut.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config.schema import Settings
from .sources import DurableSource, durable_sources

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

logger = logging.getLogger(__name__)

RECEIPT_NAME = "receipt.json"


@dataclass(frozen=True)
class SourceResult:
    """What one store contributed to a run.

    `why` is carried through from `durable_sources` so the receipt states the
    config that put this store in the run — coverage an operator can read rather
    than infer.
    """

    name: str
    kind: str
    root: str
    why: str
    artifact: str
    bytes: int
    duration_s: float
    # Per-kind detail: the models asked for (specstar) or the entries written
    # (tree). Counted from what was actually done, never from what was expected.
    models: tuple[str, ...] = ()
    files: int = 0


@dataclass(frozen=True)
class Receipt:
    """One run's evidence. Written to the destination as `receipt.json`.

    `mount_checked` is on the receipt rather than implied, so a run made with the
    precondition disabled cannot be read as a checked one later.
    """

    run_id: str
    directory: str
    started_at: str
    finished_at: str
    mount_checked: bool
    sources: list[dict[str, Any]] = field(default_factory=list)
    source_results: list[SourceResult] = field(default_factory=list)


def run_backup(settings: Settings, spec: SpecStar, *, now: dt.datetime) -> Receipt:
    """Archive every durable store this `settings` makes the app write to.

    Raises rather than writing a partial or empty archive: an archive that
    succeeds while holding less than it claims is the one failure mode a backup
    must never have, because it is discovered on the day of the restore.
    """
    dest = settings.backup.dest
    if not dest:
        raise ValueError(
            "backup.dest is unset — this deployment has no backup destination "
            "configured. Set it in config.yaml; there is no default, because the "
            "platform cannot guess where an operator wants 100 GB - 2 TB written."
        )

    sources = durable_sources(settings)
    if not sources:
        raise ValueError(
            "no durable store is configured, so there is nothing to back up. "
            "Refusing rather than writing an empty archive — an empty archive "
            "succeeds, and retention would eventually delete the ones that held "
            "real data."
        )

    checked = settings.backup.require_mounted_sources
    if checked:
        for source in sources:
            _require_mount(source)

    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(dest) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    started = dt.datetime.now(dt.UTC)
    results = [_archive(source, spec, run_dir) for source in sources]
    finished = dt.datetime.now(dt.UTC)

    receipt = Receipt(
        run_id=run_id,
        directory=str(run_dir),
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        mount_checked=checked,
        sources=[asdict(r) for r in results],
        source_results=results,
    )
    _write_receipt(run_dir, receipt)
    logger.info(
        "backup: run %s wrote %d source(s), %d bytes total",
        run_id,
        len(results),
        sum(r.bytes for r in results),
    )
    return receipt


def _require_mount(source: DurableSource) -> None:
    """Refuse a source whose root is not its own mount point.

    A volume that failed to mount leaves an ordinary empty directory behind, and
    every size- or count-based heuristic reads that as "a small backup" — `blob-gc`
    shrinks counts legitimately, so there is no threshold that separates the two.
    `ismount` separates them exactly.
    """
    if not source.root:
        return  # no local path to check (a Postgres-backed store)
    if not os.path.ismount(source.root):
        raise ValueError(
            f"backup source {source.name!r} at {source.root!r} is not a mount point. "
            "An unmounted volume looks like an empty directory, which would be "
            "archived as a successful, empty backup. Set "
            "backup.require_mounted_sources: false only where this root genuinely "
            "is not its own mount."
        )


def _archive(source: DurableSource, spec: SpecStar, run_dir: Path) -> SourceResult:
    started = time.monotonic()
    match source.kind:
        case "specstar":
            artifact, models, files = _dump_specstar(spec, run_dir, source.name)
        case "tree":
            artifact, models, files = _tar_tree(Path(source.root), run_dir, source.name)
        case other:  # pragma: no cover - `durable_sources` cannot produce another kind
            raise ValueError(f"no archiver for source kind {other!r}")
    return SourceResult(
        name=source.name,
        kind=source.kind,
        root=source.root,
        why=source.why,
        artifact=str(artifact),
        bytes=artifact.stat().st_size,
        duration_s=round(time.monotonic() - started, 3),
        models=models,
        files=files,
    )


def _dump_specstar(spec: SpecStar, run_dir: Path, name: str) -> tuple[Path, tuple[str, ...], int]:
    """specstar's own archive, streamed to a file.

    `SpecStar.dump` writes one length-prefixed frame per record straight to the
    stream, and on a disk backend `ResourceManager.dump` yields one resource at a
    time, so this holds one record in memory rather than the dataset. The ceiling
    it cannot dodge is a single blob: one blob is one record, and the encoder
    copies it once more before the write (#450 S7), so peak memory is about twice
    the largest file in the deployment.
    """
    artifact = run_dir / f"{name}.acbak"
    models = tuple(sorted(spec.resource_managers))
    with artifact.open("wb") as fh:
        spec.dump(fh)
    return artifact, models, 0


def _tar_tree(root: Path, run_dir: Path, name: str) -> tuple[Path, tuple[str, ...], int]:
    """The workspace tree as an uncompressed tar.

    Uncompressed on purpose: the contents are user documents and already-compressed
    formats, so compression would buy little and cost CPU on every pass. Counting
    happens in the filter, which is the only place that sees what actually went in
    — a separate walk to count would be a second traversal AND could disagree.
    """
    artifact = run_dir / f"{name}.tar"
    counted = 0

    def _count(info: tarfile.TarInfo) -> tarfile.TarInfo:
        nonlocal counted
        counted += 1
        return info

    with tarfile.open(artifact, "w") as tar:
        if root.exists():
            tar.add(root, arcname=".", filter=_count)
    return artifact, (), counted


def _write_receipt(run_dir: Path, receipt: Receipt) -> None:
    """Receipt last, and atomically.

    Its presence is what a later run — and the staleness sweeper — treat as "this
    run finished". A half-written receipt beside a half-written archive would make
    an interrupted run look complete.
    """
    payload = {
        "run_id": receipt.run_id,
        "directory": receipt.directory,
        "started_at": receipt.started_at,
        "finished_at": receipt.finished_at,
        "mount_checked": receipt.mount_checked,
        "sources": receipt.sources,
    }
    tmp = run_dir / f".{RECEIPT_NAME}.partial"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(run_dir / RECEIPT_NAME)
