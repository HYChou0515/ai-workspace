#!/usr/bin/env python
"""Run a specstar schema migration (index backfill / re-extract) for one or more
resource models through the app's migrate route, and print the REINDEX commands
that reclaim the space afterwards.

WHY THIS EXISTS. specstar extracts a row's ``indexed_data`` at WRITE time and
never auto-backfills. When a deploy adds a new index to an existing model — or
changes how ``indexed_data`` is derived (specstar 0.12.1 stopped copying a
``Vector`` field into it, because the field has its own pgvector column) —
existing rows keep their OLD shape until each is re-extracted. ``POST
/{model}/migrate/execute`` is the supported re-extract: it re-runs every row's
meta through the current write path.

It SKIPS any row already at the latest schema version, so the model's ``Schema``
must first be bumped with a no-op ``_reindex_only`` step to give migrate a delta.
That is a code change and is already shipped for the models this script targets.

See ``docs/migrations.md`` for the full picture and the concrete 0.12.1 cleanup.

Usage:
    # dry-run first — streams the same progress but writes nothing back:
    uv run python scripts/run_migrate.py --dry-run doc-chunk cluster-member

    # then for real (rewrites every row's meta — run during low traffic):
    uv run python scripts/run_migrate.py doc-chunk cluster-member

    # non-default host / a mounted root_path:
    uv run python scripts/run_migrate.py --base-url https://kb.example.com doc-chunk
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

import httpx


def _meta_table(model: str, prefix: str) -> str:
    """The Postgres meta table backing *model* — what you REINDEX.

    specstar names it ``<prefix><snake_model>_meta``. Our model names are already
    kebab-case, so the snake form is a ``-`` -> ``_`` swap; we defer to specstar's
    own converter when importable so the two never drift.
    """
    try:
        from specstar.resource_manager.storage_factory import _pg_safe_name

        safe = _pg_safe_name(model)
    except Exception:
        safe = model.replace("-", "_")
    return f"{prefix}{safe}_meta"


def _run_one(client: httpx.Client, base: str, model: str, dry_run: bool) -> Counter:
    """Stream one model's migrate route, tallying MigrateProgress by status."""
    verb = "test" if dry_run else "execute"
    url = f"{base}/api/{model}/migrate/{verb}"
    tally: Counter = Counter()
    print(f"\n=== {model}: POST /api/{model}/migrate/{verb} ===")
    with client.stream("POST", url) as resp:
        if resp.status_code != 200:
            resp.read()
            print(f"  ! HTTP {resp.status_code}: {resp.text[:300]}")
            tally["http_error"] += 1
            return tally
        for line in resp.iter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            status = msg.get("status", "?")
            tally[status] += 1
            # A migrating/skipped/success stream is quiet; surface only failures.
            if status in {"failed", "error"}:
                print(f"  ! {msg.get('resource_id')}: {msg.get('error') or msg.get('message')}")
            elif tally[status] % 5000 == 0:
                print(f"  … {status}: {tally[status]}")
    parts = ", ".join(f"{k}={v}" for k, v in sorted(tally.items()))
    print(f"  {model}: {parts or 'no rows'}")
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("models", nargs="+", help="resource model(s), e.g. doc-chunk cluster-member")
    ap.add_argument(
        "--base-url", default="http://localhost:8000", help="app base URL (routes live under /api)"
    )
    ap.add_argument("--dry-run", action="store_true", help="use migrate/test — no write-back")
    ap.add_argument(
        "--table-prefix",
        default="",
        help="the deploy's specstar table_prefix, if any (for the printed REINDEX)",
    )
    ap.add_argument("--timeout", type=float, default=3600.0, help="per-model stream timeout (s)")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    totals: Counter = Counter()
    failed_models: list[str] = []

    with httpx.Client(timeout=httpx.Timeout(args.timeout, connect=10.0)) as client:
        for model in args.models:
            try:
                tally = _run_one(client, base, model, args.dry_run)
            except httpx.HTTPError as exc:
                print(f"  ! {model}: request failed — {exc}")
                failed_models.append(model)
                continue
            totals.update(tally)
            if tally.get("failed") or tally.get("error") or tally.get("http_error"):
                failed_models.append(model)

    print("\n=== summary ===")
    print(", ".join(f"{k}={v}" for k, v in sorted(totals.items())) or "no rows processed")

    if args.dry_run:
        print("\n(dry-run: nothing was written; re-run without --dry-run to apply)")
        return 1 if failed_models else 0

    if not failed_models:
        print("\nRewrite done. Reclaim the index space (rebuilds each meta table's")
        print("indexes online; safe to run one at a time during low traffic):")
        for model in args.models:
            print(f"  REINDEX TABLE CONCURRENTLY {_meta_table(model, args.table_prefix)};")

    if failed_models:
        print(f"\nFAILED for: {', '.join(failed_models)} — see the '!' lines above.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
