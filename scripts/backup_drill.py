#!/usr/bin/env python
"""Backup drill (docs/plan-backup.md P8): prove a restore works, on a real deployment.

A backup nobody has restored is a directory of files that costs money. Unit tests
prove the round trip on `tmp_path`; they cannot prove it on the deployment's own
storage, its own config, its own volumes. This drives the REAL entry points —
`python -m workspace_app.backup` and `python -m workspace_app.restore` — against
two configs, and then reads the result back through the app's own filestore.

It also measures the one thing the plan deliberately left unmeasured:
**what a run catches while the deployment is still being written to.** specstar's
documentation states no consistency guarantee for `dump`, and reasoning about it
on paper is not evidence. So the drill writes continuously THROUGH the backup and
reports three numbers:

    before  — files that existed before the run started.  MUST all come back:
              anything less is data loss, not a consistency subtlety.
    during  — files written while the run was in progress. Some will land in the
              archive and some will not; the number is the answer to "how wide is
              the window", and a chain's next increment picks up the rest.
    after   — files written once the run finished. MUST NOT be present: if they
              are, the window boundary is not where the receipt says it is.

Only `before` and `after` are pass/fail. `during` is reported, because a number
somebody can act on beats a rule nobody measured.

USAGE

    python scripts/backup_drill.py \
        --source-config /etc/rca/config.yaml \
        --target-config /etc/rca/config.restore.yaml \
        [--files 200] [--writers 4]

The two configs must name the SAME `backup.dest` and DIFFERENT
`filestore.disk_root` (and different `sandbox.durable.nfs_root`, if set) — the
drill refuses otherwise, because restoring onto the source is not a drill.

On staging this is safe to run repeatedly. It is NOT safe against production:
the restore overwrites the target deployment's store.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from workspace_app.backup import run_backup  # noqa: E402
from workspace_app.backup.__main__ import build_backup_spec  # noqa: E402
from workspace_app.backup.restore import restore_chain  # noqa: E402
from workspace_app.config.loader import load_with_provenance  # noqa: E402
from workspace_app.config.schema import Settings  # noqa: E402
from workspace_app.filestore.specstar_impl import SpecstarFileStore  # noqa: E402

WORKSPACE = "backup-drill"


def _load(path: Path) -> Settings:
    settings, _ = load_with_provenance(config_path=path)
    return settings


def _refuse_same_store(source: Settings, target: Settings) -> None:
    """Restoring onto the source is not a drill, it is an outage.

    Checked here rather than left to judgement: the two configs differ by one
    line, and the one line is easy to forget when copying a file.
    """
    if source.filestore.disk_root == target.filestore.disk_root:
        raise SystemExit(
            "refusing: both configs name the same filestore.disk_root "
            f"({source.filestore.disk_root!r}). The drill would restore over the "
            "deployment it just backed up."
        )
    if source.backup.dest != target.backup.dest:
        raise SystemExit(
            "refusing: the configs name different backup.dest values "
            f"({source.backup.dest!r} vs {target.backup.dest!r}), so the restore "
            "would read a destination the backup never wrote to."
        )
    src_tree = source.sandbox.durable.nfs_root
    dst_tree = target.sandbox.durable.nfs_root
    if src_tree and src_tree == dst_tree:
        raise SystemExit(f"refusing: both configs name the same workspace tree ({src_tree!r}).")


def _payload(tag: str, n: int) -> bytes:
    return f"drill:{tag}:{n}:".encode() + bytes([n % 251]) * 512


def _write_batch(files: SpecstarFileStore, tag: str, count: int) -> list[str]:
    paths = []
    for n in range(count):
        path = f"/{tag}/{n:05d}.bin"
        asyncio.run(files.write(WORKSPACE, path, _payload(tag, n)))
        paths.append(path)
    return paths


class _Writer(threading.Thread):
    """Keeps writing until told to stop. Records what it managed to write.

    A thread rather than a burst before the run: the question is what happens to
    writes that land WHILE the archive is being produced, and a burst that
    finishes first answers a different question.
    """

    def __init__(self, files: SpecstarFileStore) -> None:
        super().__init__(daemon=True)
        self._files = files
        self._halt = threading.Event()
        self.written: list[str] = []

    def run(self) -> None:
        n = 0
        while not self._halt.is_set():
            path = f"/during/{n:05d}.bin"
            try:
                asyncio.run(self._files.write(WORKSPACE, path, _payload("during", n)))
            except Exception as exc:  # pragma: no cover - drill script
                print(f"  writer: {type(exc).__name__}: {exc}", file=sys.stderr)
                return
            self.written.append(path)
            n += 1
            time.sleep(0.01)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=10)


def _present(files: SpecstarFileStore, path: str, expected: bytes) -> bool:
    try:
        return asyncio.run(files.read(WORKSPACE, path)) == expected
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--source-config", type=Path, required=True)
    p.add_argument("--target-config", type=Path, required=True)
    p.add_argument("--files", type=int, default=200, help="how many files to seed before the run")
    args = p.parse_args(argv)

    source = _load(args.source_config)
    target = _load(args.target_config)
    _refuse_same_store(source, target)

    print("drill: composing the source deployment")
    source_spec = build_backup_spec(source, config_dir=args.source_config.parent)
    source_files = SpecstarFileStore(source_spec)

    print(f"drill: seeding {args.files} file(s) BEFORE the run")
    before = _write_batch(source_files, "before", args.files)

    print("drill: starting a writer, then running the backup through it")
    writer = _Writer(source_files)
    writer.start()
    started = time.monotonic()
    receipt = run_backup(source, source_spec, now=dt.datetime.now(dt.UTC))
    elapsed = time.monotonic() - started
    writer.stop()
    during = list(writer.written)
    print(
        f"drill: run {receipt.run_id} ({receipt.kind}, chain {receipt.chain}) "
        f"took {elapsed:.1f}s, verified {receipt.verified_blobs} blob reference(s); "
        f"{len(during)} file(s) were written while it ran"
    )

    print("drill: writing a batch AFTER the run, which must NOT appear")
    after = _write_batch(source_files, "after", 5)

    print("drill: restoring into the target deployment")
    target_spec = build_backup_spec(target, config_dir=args.target_config.parent)
    report = restore_chain(target, target_spec, confirm=True, chain=receipt.chain)
    print(
        f"drill: replayed chain {report.chain} — {report.archives_loaded} archive(s), "
        f"{report.trees_extracted} tree(s), sources {', '.join(report.sources)}"
    )

    target_files = SpecstarFileStore(target_spec)
    missing_before = [
        path
        for n, path in enumerate(before)
        if not _present(target_files, path, _payload("before", n))
    ]
    caught_during = sum(
        1 for n, path in enumerate(during) if _present(target_files, path, _payload("during", n))
    )
    leaked_after = [
        path for n, path in enumerate(after) if _present(target_files, path, _payload("after", n))
    ]

    print()
    print(f"  before : {len(before) - len(missing_before)}/{len(before)} restored")
    print(f"  during : {caught_during}/{len(during)} caught by the run in flight")
    print(f"  after  : {len(leaked_after)}/{len(after)} present (must be 0)")
    print()

    failed = False
    if missing_before:
        failed = True
        print(
            f"FAIL: {len(missing_before)} file(s) that existed before the run did not "
            f"come back, e.g. {missing_before[0]}. That is data loss, not a "
            "consistency subtlety."
        )
    if leaked_after:
        failed = True
        print(
            f"FAIL: {len(leaked_after)} file(s) written AFTER the run are in the "
            "archive, so the window boundary is not where the receipt says it is."
        )
    if not failed:
        print("PASS: everything that existed before the run came back, and nothing after it did.")
        print(
            "Note the `during` number: it is how wide the concurrent-write window is "
            "on THIS deployment. The next increment in the chain picks up the rest — "
            "which is the property that matters, not that the number is high."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
