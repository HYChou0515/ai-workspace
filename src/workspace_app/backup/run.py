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
from .ledger import BackupLedger
from .sources import DurableSource, durable_sources
from .tree import Manifest, tar_tree
from .verify import verify_archives

if TYPE_CHECKING:  # pragma: no cover - typing only
    from specstar import SpecStar

logger = logging.getLogger(__name__)

RECEIPT_NAME = "receipt.json"

#: How a run id — and therefore a chain id — is spelled. ONE constant, shared by
#: the code that writes it and the code that reads it back, because the first
#: version of this had `strftime("%Y%m%dT%H%M%SZ")` on one side and
#: `strptime(chain[:16], "%Y%m%dT%H%M%S")` on the other. A real id is exactly
#: sixteen characters INCLUDING the `Z`, so the slice kept it, `strptime` raised,
#: an `except` swallowed it, and chain rotation silently never happened.
_RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"
_RUN_ID_LEN = 16

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
    # Tree sources only: where this run's walk was recorded, and what it could
    # not carry. `skipped` is named rather than counted — a number with no
    # names is not something an operator can act on.
    manifest: str = ""
    skipped: tuple[str, ...] = ()


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
    # How many live blob references were checked against this run's own archives.
    # Recorded rather than implied: a sample of zero passes every check ever
    # written, so "verified" has to be distinguishable from "found nothing to
    # verify".
    verified_blobs: int
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

    for source in sources:
        _require_readable(source)

    run_id = _unique_run_id(dest, now)
    previous = None if full else _latest_receipt(dest)
    if previous is not None and _coverage_changed(previous, sources):
        # The day an operator turns on `nfs_tree`, continuing the chain would
        # give it a full covering {specstar} and increments covering
        # {specstar, sandbox-workspaces}. The restore checks coverage PER RUN —
        # a union across the chain hides exactly this — so such a chain is
        # refused with the old config AND with the new one. Unrestorable with
        # any config, forever, while the nightly run keeps reporting success.
        logger.info(
            "backup: the set of durable stores changed since chain %s; starting a new one",
            previous["chain"],
        )
        previous = None
    elif previous is not None and _chain_is_stale(previous, now, settings.backup.full_every_days):
        # Retention deletes along chains, so a deployment that never starts a new
        # one can never delete anything: `keep_chains` is set, nothing is ever
        # pruned, and the destination grows until it is full. Rotating on a
        # schedule is what makes the knob reachable at all.
        logger.info(
            "backup: chain %s is older than full_every_days; starting a new one", previous["chain"]
        )
        previous = None
    if previous is None:
        kind, chain, parent = "full", run_id, None
        window_start = since if since is not None else _earliest_updated(spec)
    else:
        kind, chain, parent = "incremental", str(previous["chain"]), str(previous["run_id"])
        window_start = since if since is not None else _parse(str(previous["window_end"]))

    if window_start is not None and window_start > now:
        # Checked BEFORE the directory exists. Raising after `mkdir` but before
        # the try/except that cleans up left an orphan directory on every retry.
        raise ValueError(
            f"the window would start at {window_start.isoformat()} and end at "
            f"{now.isoformat()} — it ends before it begins, so every archive "
            "would be empty and the run would report success. Check --since, and "
            "check the clock on this pod against the pods that write."
        )

    run_dir = dest / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = dt.datetime.now(dt.UTC)

    try:
        results, verified = _write_archives(
            settings, spec, sources, run_dir, kind, previous, window_start, now
        )
    except BaseException:
        # A run that did not finish must not leave its archives behind. Nothing
        # ever reclaims them: `_prune_chains` walks receipts, and a failed run
        # has none — so on a nightly schedule with one retry, two partial runs
        # per night accumulate until the destination is full, at which point
        # every future run dies on ENOSPC and leaves another one.
        shutil.rmtree(run_dir, ignore_errors=True)
        raise

    finished = dt.datetime.now(dt.UTC)

    receipt = Receipt(
        run_id=run_id,
        directory=str(run_dir),
        chain=chain,
        parent=parent,
        kind=kind,
        window_start=window_start.isoformat() if window_start else None,
        window_end=now.isoformat(),
        started_at=started_at.isoformat(),
        finished_at=finished.isoformat(),
        mount_checked=checked,
        verified_blobs=verified,
        sources=[asdict(r) for r in results],
        source_results=results,
    )
    _write_receipt(run_dir, receipt)
    # Everything past the receipt is bookkeeping, and bookkeeping must not turn
    # a good backup into a reported failure. The archives are on disk and the
    # receipt says so; a store hiccup here would otherwise print `backup: FAILED`
    # over a complete, restorable run.
    try:
        BackupLedger(spec).record(receipt)
    except Exception:
        logger.exception(
            "backup: run %s is complete on disk but could not be recorded in the "
            "ledger. The staleness sweeper will read this deployment as having no "
            "recent backup until the next run records one.",
            run_id,
        )
    try:
        _prune_chains(dest, settings.backup.keep_chains, keep=chain)
    except Exception:
        logger.exception("backup: retention pass failed after run %s", run_id)
    logger.info(
        "backup: %s run %s (chain %s) wrote %d archive(s), %d bytes total",
        kind,
        run_id,
        chain,
        len(results),
        sum(r.bytes for r in results),
    )
    return receipt


def _write_archives(
    settings: Settings,
    spec: SpecStar,
    sources: tuple[DurableSource, ...],
    run_dir: Path,
    kind: str,
    previous: dict[str, Any] | None,
    window_start: dt.datetime | None,
    now: dt.datetime,
) -> tuple[list[SourceResult], int]:
    """Produce every archive for this run, and prove they hold what they claim."""
    windows = _windows(window_start, now, settings.backup.slice_days)
    results: list[SourceResult] = []
    for source in sources:
        if source.kind == "specstar":
            for seq, window in enumerate(windows):
                results.append(_archive(source, spec, run_dir, seq=seq, window=window))
            continue
        # A tree is NOT sliced, and the reason is what slicing is for: bounding
        # the memory `SpecStar.load` needs. Extracting a tar is streaming, so
        # there is nothing to bound. One archive per run.
        #
        # Nor is it filtered by an mtime lower bound. Change detection is a
        # MANIFEST DIFF, because mtime is not a change detector: `unzip`,
        # `tar x`, `cp -p` and a restore all stamp a brand-new file with an old
        # timestamp, and an mtime floor drops every one of them — into no
        # archive at all, since every later window starts later still. Diffing
        # against the previous run's `{path: (size, mtime)}` catches them as
        # what they are: paths that were not there before. `window_end` stays,
        # because writes after it genuinely belong to the next run.
        previous_manifest = None if kind == "full" else _previous_manifest(previous, source.name)
        results.append(
            _archive(source, spec, run_dir, seq=0, window=(None, now), previous=previous_manifest)
        )
    # Verify BEFORE the receipt: the receipt's presence is what marks a run
    # complete, so a run that cannot prove its archives hold what they reference
    # must not leave one behind.
    verified = _verify(spec, results, window_start, now, settings.backup.verify_sample)
    return results, verified


def _verify(
    spec: SpecStar,
    results: list[SourceResult],
    start: dt.datetime | None,
    end: dt.datetime,
    sample: int,
) -> int:
    """Check a sample of this run's own blob references against its archives.

    Sampled over the run's whole window and checked across all of its specstar
    archives, so a reference that landed in a different slice than expected still
    counts as present. Sampling outside the window would fail an incremental for
    records it was never meant to carry.
    """
    if sample <= 0:
        return 0
    archives = [Path(r.artifact) for r in results if r.kind == "specstar"]
    if not archives:
        return 0

    def _query_for(_name: str) -> ResourceMetaSearchQuery:
        if start is None:
            return ResourceMetaSearchQuery(limit=sample)
        return ResourceMetaSearchQuery(updated_time_start=start, updated_time_end=end, limit=sample)

    return verify_archives(spec, _query_for, archives, limit=sample)


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
    precisely the restore that `load` cannot hold in memory.

    ⚠️ NOT a cheap query on the backend this deployment runs. `limit=1` with a
    sort is an index scan on Postgres, but `DiskMetaStore.iter_search` has no
    index: it reads and decodes every meta file of the model and sorts them all
    in Python before applying the limit. On a disk store this is a full metadata
    scan per model, and it is the FIRST thing a full run does. Bounding the
    archive is still worth it — an unbounded archive cannot be restored at all —
    but the cost is real and belongs in the pod's memory and time budget.
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


def _require_readable(source: DurableSource) -> None:
    """Refuse a source whose root is not a directory we can walk.

    Distinct from `_require_mount`, and needed even when that check is off. A
    tree root that does not exist walks to nothing, tars to an empty archive,
    and passes the coverage check on the way back — because coverage compares
    source NAMES and the name is there. The restore then writes an empty tree
    over a deployment and reports success. An absent root is not "an empty
    workspace"; it is a mount or a path that is wrong.
    """
    if source.kind != "tree" or not source.root:
        return
    root = Path(source.root)
    if not root.is_dir():
        raise ValueError(
            f"backup source {source.name!r} at {source.root!r} is not a directory. "
            "Archiving it would produce an empty tar that restores as an empty "
            "workspace tree, and nothing downstream can tell that apart from a "
            "deployment that genuinely has no files."
        )


def _coverage_changed(previous: dict[str, Any], sources: tuple[DurableSource, ...]) -> bool:
    """Does this run cover a different set of stores than the previous one did?"""
    was = {str(s["name"]) for s in previous.get("sources", [])}
    now_covering = {s.name for s in sources}
    return was != now_covering


def _chain_is_stale(previous: dict[str, Any], now: dt.datetime, full_every_days: int) -> bool:
    """Has the current chain's full run aged past `full_every_days`?

    Retention deletes along chains. A deployment that never starts a second one
    can therefore never delete anything — `keep_chains` is set, nothing is
    pruned, and the destination fills. Rotating on a schedule is what makes the
    knob reachable, and it also bounds how many archives a restore must replay.
    """
    if full_every_days <= 0:
        return False
    try:
        began = dt.datetime.strptime(str(previous["chain"])[:_RUN_ID_LEN], _RUN_ID_FORMAT).replace(
            tzinfo=dt.UTC
        )
    except (KeyError, ValueError):
        # An unparseable chain id is not a reason to start a new chain — that
        # would abandon the archives retention is counting. Leave it alone and
        # let the operator's `--full` decide.
        return False
    return now - began >= dt.timedelta(days=full_every_days)


def _archive(
    source: DurableSource,
    spec: SpecStar,
    run_dir: Path,
    *,
    seq: int,
    window: Window,
    previous: Manifest | None = None,
) -> SourceResult:
    started = time.monotonic()
    stem = f"{source.name}-{seq:04d}"
    manifest_path = ""
    skipped: tuple[str, ...] = ()
    match source.kind:
        case "specstar":
            artifact, models, files = _dump_specstar(spec, run_dir, stem, window)
        case "tree":
            artifact = run_dir / f"{stem}.tar"
            _start, end = window
            result = tar_tree(Path(source.root), artifact, previous=previous, window_end=end)
            models, files, skipped = (), result.files, result.skipped
            manifest_path = str(_write_manifest(run_dir, stem, result.manifest))
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
        manifest=manifest_path,
        skipped=skipped,
    )


def _write_manifest(run_dir: Path, stem: str, manifest: Manifest) -> Path:
    """The tree as this run walked it, beside the tar rather than in the receipt.

    A receipt an operator reads should stay readable; a workspace tree can hold
    hundreds of thousands of paths. Written as a separate file so the next run
    can diff against it without the receipt growing without bound.
    """
    path = run_dir / f"{stem}.manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return path


def _previous_manifest(receipt: dict[str, Any] | None, name: str) -> Manifest | None:
    """The manifest the previous run left for source `name`, or None for a full.

    A manifest that cannot be read is treated as absent, which costs a full tree
    pass and carries everything — expensive, never wrong. That is the right way
    round: the alternative is skipping files because a JSON file was corrupt.
    """
    if receipt is None:
        return None
    for source in receipt.get("sources", []):
        if source.get("name") != name or not source.get("manifest"):
            continue
        try:
            raw = json.loads(Path(str(source["manifest"])).read_text(encoding="utf-8"))
            # The conversion belongs INSIDE the try. It used to sit outside, so a
            # structurally wrong manifest (`{"./x": [1]}`, or a list at the top
            # level) raised IndexError / AttributeError, killed the run and had
            # its directory rmtree'd — the exact opposite of the sentence above.
            return {str(k): (int(v[0]), int(v[1]), int(v[2])) for k, v in raw.items()}
        except (OSError, ValueError, TypeError, IndexError, AttributeError, KeyError):
            logger.warning(
                "backup: previous manifest for %s is unreadable; this run carries the "
                "whole tree rather than risking a skipped file",
                name,
            )
            return None
    return None


def _dump_specstar(
    spec: SpecStar, run_dir: Path, stem: str, window: Window
) -> tuple[Path, tuple[str, ...], int]:
    """specstar's own archive for one window, streamed to a file.

    `SpecStar.dump` writes one length-prefixed frame per record straight to the
    stream, and on a disk backend `ResourceManager.dump` yields one REVISION
    PAYLOAD at a time (`resource_store/simple.py` has no `dump_all_revisions`, so
    the bulk path is never taken). That is the part that streams.

    ⚠️ The rest does not, and an earlier version of this docstring claimed it did.
    `ResourceManager.dump` does `metas_list = list(metas)` plus a frozenset of
    every resource id and a set of every referenced blob id — per model, per
    window — and underneath it `DiskMetaStore.iter_search` reads and decodes
    EVERY meta file of the model, filters in Python and sorts, with no index.
    This repo already documents that exact pattern as an OOM cause in
    `filestore/blob_gc.py`. Slicing bounds the retained metas per window; it does
    not bound the read.

    So peak memory is: the largest single blob, twice over (one record, encoded
    once more before the write — #450 S7), PLUS one model-window's metas. Size
    the pod from a measurement on the real store, not from either half alone.
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


# ── receipts, chains, retention ──────────────────────────────────────────


def _unique_run_id(dest: Path, now: dt.datetime) -> str:
    """A run id no earlier run already took.

    Two runs inside the same second are a test's problem more than an operator's,
    but a collision would have the second run write into the first one's
    directory and overwrite its receipt — losing a link out of the middle of a
    chain, which is the one thing retention is careful never to do.
    """
    base = now.strftime(_RUN_ID_FORMAT)
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
        "verified_blobs": receipt.verified_blobs,
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
