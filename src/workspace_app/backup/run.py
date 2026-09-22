"""Perform one backup run.

P3 + P4 of `docs/plan-backup.md`. The specstar store travels as specstar's own
`.acbak` archive — blob bytes inline, so the archive is self-contained and
nothing here needs to understand the on-disk layout (the sharded directories, the
revision symlinks, the U+2215 in a path). The sandbox's workspace tree travels as
a tar, because it is an ordinary file tree and nothing else claims to own it.

**Slicing is a correctness requirement, not a performance one.** `SpecStar.load`
accumulates a model's records in memory and flushes only at `ModelEndRecord`
(specstar#450 S1), so the memory a restore needs is one model's data *in one
archive*. Splitting by model would not help — the buffer already clears at each
`ModelStartRecord` — so the axis that bounds it is time. Cutting a run into
`slice_days` windows makes `ModelEndRecord` arrive once per window instead of
once per dataset. Incremental backup falls out of the same cut; it is the side
effect, not the goal.

Archives are written straight into the destination rather than staged locally
first: at this size a staging copy would need as much free disk as the archive,
on a pod.

What this deliberately does NOT do is go over HTTP. specstar's own
`/_backup/export` buffers the whole archive into a `BytesIO` before it answers
(#450 S5), and `api/app.py` fences that door shut.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import tarfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from specstar.query_types import (
    ResourceMetaSearchQuery,
    ResourceMetaSearchSort,
    ResourceMetaSortDirection,
    ResourceMetaSortKey,
)

from ..config.schema import Settings
from .sources import DurableSource, durable_sources

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

logger = logging.getLogger(__name__)

RECEIPT_NAME = "receipt.json"

# The windows are half-open: a slice ends one tick before the next begins, so a
# record written exactly on a boundary lands in exactly one archive. specstar's
# `updated_time_end` is inclusive, hence the subtraction rather than a `<`.
_TICK = dt.timedelta(microseconds=1)

Window = tuple[dt.datetime | None, dt.datetime]


def _parse(stamp: str) -> dt.datetime:
    """A timestamp out of a receipt, back as an aware datetime.

    Receipts are written with `isoformat()` on aware values, so a naive result
    means the file was hand-edited — and comparing naive to aware would raise
    somewhere far from here.
    """
    parsed = dt.datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        raise ValueError(f"backup receipt holds a timestamp with no timezone: {stamp!r}")
    return parsed


@dataclass(frozen=True)
class SourceResult:
    """What one store contributed for one window.

    A source with several windows produces several of these — the slicing is
    visible in the receipt rather than hidden inside one number, because a
    restore has to replay them and an operator has to be able to see that it can.

    `why` is carried through from `durable_sources` so the receipt states the
    config that put this store in the run: coverage an operator can read rather
    than infer.
    """

    name: str
    kind: str
    root: str
    why: str
    artifact: str
    bytes: int
    duration_s: float
    seq: int = 0
    window_start: str | None = None
    window_end: str | None = None
    # Per-kind detail: the models asked for (specstar) or the entries written
    # (tree). Counted from what was actually done, never from what was expected.
    models: tuple[str, ...] = ()
    files: int = 0


@dataclass(frozen=True)
class Receipt:
    """One run's evidence. Written to the destination as `receipt.json`.

    `chain` names the full run this one builds on, and is what retention deletes
    along: dropping the oldest *run* would delete the full that every later
    increment needs, leaving a directory of archives that restores nothing.

    `mount_checked` is recorded rather than implied, so a run made with the
    precondition disabled cannot be read later as a checked one.
    """

    run_id: str
    directory: str
    chain: str
    parent: str | None
    kind: str  # "full" | "incremental"
    window_start: str | None
    window_end: str
    started_at: str
    finished_at: str
    mount_checked: bool
    sources: list[dict[str, Any]] = field(default_factory=list)
    source_results: list[SourceResult] = field(default_factory=list)


def run_backup(
    settings: Settings,
    spec: SpecStar,
    *,
    now: dt.datetime,
    since: dt.datetime | None = None,
    full: bool = False,
) -> Receipt:
    """Archive every durable store this `settings` makes the app write to.

    With no prior run — or with `full=True` — this starts a new chain and covers
    everything up to `now`. Otherwise it continues the newest chain from where
    that chain left off. `since` overrides the lower bound, which is how an
    operator bounds an initial full over a long history.

    Raises rather than writing a partial or empty archive: an archive that
    succeeds while holding less than it claims is the one failure mode a backup
    must never have, because it is discovered on the day of the restore.
    """
    dest_setting = settings.backup.dest
    if not dest_setting:
        raise ValueError(
            "backup.dest is unset — this deployment has no backup destination "
            "configured. Set it in config.yaml; there is no default, because the "
            "platform cannot guess where an operator wants 100 GB - 2 TB written."
        )
    dest = Path(dest_setting)

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

    run_id = _unique_run_id(dest, now)
    previous = None if full else _latest_receipt(dest)
    if previous is None:
        kind, chain, parent = "full", run_id, None
        window_start = since if since is not None else _earliest_updated(spec)
    else:
        kind, chain, parent = "incremental", str(previous["chain"]), str(previous["run_id"])
        window_start = since if since is not None else _parse(str(previous["window_end"]))

    run_dir = dest / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    windows = _windows(window_start, now, settings.backup.slice_days)
    started = dt.datetime.now(dt.UTC)
    results: list[SourceResult] = []
    for source in sources:
        for seq, window in enumerate(windows):
            results.append(_archive(source, spec, run_dir, seq=seq, window=window))
    finished = dt.datetime.now(dt.UTC)

    receipt = Receipt(
        run_id=run_id,
        directory=str(run_dir),
        chain=chain,
        parent=parent,
        kind=kind,
        window_start=window_start.isoformat() if window_start else None,
        window_end=now.isoformat(),
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        mount_checked=checked,
        sources=[asdict(r) for r in results],
        source_results=results,
    )
    _write_receipt(run_dir, receipt)
    _prune_chains(dest, settings.backup.keep_chains, keep=chain)
    logger.info(
        "backup: %s run %s (chain %s) wrote %d archive(s), %d bytes total",
        kind,
        run_id,
        chain,
        len(results),
        sum(r.bytes for r in results),
    )
    return receipt


def chain_of(dest: Path | str, chain: str) -> list[str]:
    """Every archive in `chain`, in the order a restore must load them.

    Oldest run first, and within a run the windows in sequence, because `load`
    applies records with `on_duplicate=overwrite` — replaying them newest-first
    would leave the oldest version of every record standing.
    """
    runs = sorted(
        (r for r in _receipts(Path(dest)) if str(r["chain"]) == chain),
        key=lambda r: str(r["run_id"]),
    )
    return [
        str(source["artifact"])
        for run in runs
        for source in sorted(run["sources"], key=lambda s: (s["name"] != "specstar", s["seq"]))
    ]


# ── windows ──────────────────────────────────────────────────────────────


def _windows(start: dt.datetime | None, end: dt.datetime, slice_days: int) -> list[Window]:
    """Cut `[start, end]` into slices no longer than `slice_days`.

    An unknown lower bound (an empty deployment, or a store with no timestamps to
    read) gives one open-ended window: there is nothing to slice.
    """
    if start is None or slice_days <= 0 or start >= end:
        return [(start, end)]
    width = dt.timedelta(days=slice_days)
    windows: list[Window] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + width, end)
        windows.append((cursor, stop if stop >= end else stop - _TICK))
        cursor = stop
    return windows


def _earliest_updated(spec: SpecStar) -> dt.datetime | None:
    """The oldest `updated_time` in the store, so an initial full can be sliced.

    Without a lower bound the first run is one unbounded archive, which is
    precisely the restore that `load` cannot hold in memory. One indexed,
    limit-one query per model is a cheap price for bounding it.
    """
    oldest: dt.datetime | None = None
    for name in spec.resource_managers:
        query = ResourceMetaSearchQuery(
            limit=1,
            sorts=[
                ResourceMetaSearchSort(
                    key=ResourceMetaSortKey.updated_time,
                    direction=ResourceMetaSortDirection.ascending,
                )
            ],
        )
        try:
            rows = list(spec.resource_managers[name].list_resources(query))
        except Exception:  # pragma: no cover - a backend that cannot sort
            logger.warning("backup: %s could not report its oldest record", name, exc_info=True)
            return None
        for row in rows:
            stamp = getattr(row.meta, "updated_time", None)
            if isinstance(stamp, dt.datetime) and (oldest is None or stamp < oldest):
                oldest = stamp
    return oldest


# ── archiving ────────────────────────────────────────────────────────────


def _require_mount(source: DurableSource) -> None:
    """Refuse a source whose root is not its own mount point.

    A volume that failed to mount leaves an ordinary empty directory behind, and
    every size- or count-based heuristic reads that as "a small backup" —
    `blob-gc` shrinks counts legitimately, so there is no threshold that
    separates the two. `ismount` separates them exactly.
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


def _archive(
    source: DurableSource, spec: SpecStar, run_dir: Path, *, seq: int, window: Window
) -> SourceResult:
    started = time.monotonic()
    stem = f"{source.name}-{seq:04d}"
    match source.kind:
        case "specstar":
            artifact, models, files = _dump_specstar(spec, run_dir, stem, window)
        case "tree":
            artifact, models, files = _tar_tree(Path(source.root), run_dir, stem, window)
        case other:  # pragma: no cover - `durable_sources` cannot produce another kind
            raise ValueError(f"no archiver for source kind {other!r}")
    start, end = window
    return SourceResult(
        name=source.name,
        kind=source.kind,
        root=source.root,
        why=source.why,
        artifact=str(artifact),
        bytes=artifact.stat().st_size,
        duration_s=round(time.monotonic() - started, 3),
        seq=seq,
        window_start=start.isoformat() if start else None,
        window_end=end.isoformat(),
        models=models,
        files=files,
    )


def _dump_specstar(
    spec: SpecStar, run_dir: Path, stem: str, window: Window
) -> tuple[Path, tuple[str, ...], int]:
    """specstar's own archive for one window, streamed to a file.

    `SpecStar.dump` writes one length-prefixed frame per record straight to the
    stream, and on a disk backend `ResourceManager.dump` yields one resource at a
    time, so this holds one record in memory rather than the dataset. The ceiling
    it cannot dodge is a single blob: one blob is one record, and the encoder
    copies it once more before the write (#450 S7), so peak memory is about twice
    the largest file in the deployment.
    """
    artifact = run_dir / f"{stem}.acbak"
    models = tuple(sorted(spec.resource_managers))
    start, end = window
    queries: dict[str, Any] | None = None
    if start is not None:
        queries = {
            name: ResourceMetaSearchQuery(updated_time_start=start, updated_time_end=end)
            for name in models
        }
    with artifact.open("wb") as fh:
        spec.dump(fh, model_queries=queries)
    return artifact, models, 0


def _tar_tree(
    root: Path, run_dir: Path, stem: str, window: Window
) -> tuple[Path, tuple[str, ...], int]:
    """The workspace tree, as an uncompressed tar of what the window covers.

    Uncompressed on purpose: the contents are user documents and already
    compressed formats, so compression would buy little and cost CPU on every
    pass. Counting happens in the filter, the only place that sees what actually
    went in — a separate walk to count would be a second traversal AND could
    disagree with it.

    Directories always ride along whatever the window is: a tar that carries a
    file without its parent restores into nothing.
    """
    artifact = run_dir / f"{stem}.tar"
    start, end = window
    counted = 0

    def _keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal counted
        if not info.isdir() and start is not None:
            stamp = dt.datetime.fromtimestamp(info.mtime, dt.UTC)
            if stamp < start or stamp > end:
                return None
        counted += 1
        return info

    with tarfile.open(artifact, "w") as tar:
        if root.exists():
            tar.add(root, arcname=".", filter=_keep)
    return artifact, (), counted


# ── receipts, chains, retention ──────────────────────────────────────────


def _unique_run_id(dest: Path, now: dt.datetime) -> str:
    """A run id no earlier run already took.

    Two runs inside the same second are a test's problem more than an operator's,
    but a collision would have the second run write into the first one's
    directory and overwrite its receipt — losing a link out of the middle of a
    chain, which is the one thing retention is careful never to do.
    """
    base = now.strftime("%Y%m%dT%H%M%SZ")
    candidate, n = base, 1
    while (dest / candidate).exists():
        candidate = f"{base}-{n:02d}"
        n += 1
    return candidate


def _receipts(dest: Path) -> list[dict[str, Any]]:
    """Every complete run in `dest`, oldest first.

    A directory without a `receipt.json` is an interrupted run, not a link in a
    chain: the receipt is written last and atomically precisely so it can mean
    "this finished". A receipt that cannot be parsed is an error rather than a
    skip — treating it as absent would silently start a new chain and abandon the
    one retention is counting.
    """
    if not dest.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for run_dir in sorted(dest.iterdir()):
        path = run_dir / RECEIPT_NAME
        if not path.is_file():
            continue
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"backup receipt {path} cannot be read ({exc}). Refusing to continue: "
                "treating it as missing would silently start a new chain and strand "
                "the archives an operator's retention is counting on."
            ) from exc
    return found


def _latest_receipt(dest: Path) -> dict[str, Any] | None:
    receipts = _receipts(dest)
    return receipts[-1] if receipts else None


def _write_receipt(run_dir: Path, receipt: Receipt) -> None:
    """Receipt last, and atomically.

    Its presence is what a later run — and the staleness sweeper — treat as "this
    run finished". A half-written receipt beside a half-written archive would make
    an interrupted run look complete.
    """
    payload = {
        "run_id": receipt.run_id,
        "directory": receipt.directory,
        "chain": receipt.chain,
        "parent": receipt.parent,
        "kind": receipt.kind,
        "window_start": receipt.window_start,
        "window_end": receipt.window_end,
        "started_at": receipt.started_at,
        "finished_at": receipt.finished_at,
        "mount_checked": receipt.mount_checked,
        "sources": receipt.sources,
    }
    tmp = run_dir / f".{RECEIPT_NAME}.partial"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(run_dir / RECEIPT_NAME)


def _prune_chains(dest: Path, keep_chains: int, *, keep: str) -> None:
    """Keep the newest `keep_chains` chains; delete the rest whole.

    Along chains, never along runs. Deleting the oldest *run* would take the full
    that every later increment builds on, leaving a directory of archives that
    restores nothing — and it would look like it worked, because the directory is
    still full of files.

    `0` keeps everything, and that is the default: a retention policy that starts
    deleting the moment someone sets a destination is a policy nobody chose.
    """
    if keep_chains <= 0:
        return
    order: list[str] = []
    for receipt in _receipts(dest):
        chain = str(receipt["chain"])
        if chain not in order:
            order.append(chain)
    survivors = set(order[-keep_chains:]) | {keep}
    for receipt in _receipts(dest):
        if str(receipt["chain"]) in survivors:
            continue
        run_dir = dest / str(receipt["run_id"])
        logger.info("backup: pruning run %s (chain %s)", receipt["run_id"], receipt["chain"])
        shutil.rmtree(run_dir, ignore_errors=True)
