"""The app opts into specstar's bulk-migration route template, so an operator
can backfill pre-index rows into newly-added indexes via HTTP.

This is the supported replacement for a hand-rolled reindex loop: after an
index is added (e.g. `content_size` on SourceDoc, the CitationEvent indexes),
rows written before it exists are version `None` and under-count in aggregates
until migrated. `POST /{model}/migrate/execute` re-extracts their indexed_data
(write_back) — see specstar discussion #365. We register the template globally,
so every model gets the routes.
"""

from __future__ import annotations

from fastapi import FastAPI

from workspace_app.resources import make_spec


def _mounted_paths() -> set[str]:
    app = FastAPI()
    make_spec().apply(app)
    return {p for r in app.routes if (p := getattr(r, "path", None)) is not None}


def test_app_mounts_migrate_execute_for_backfill_targets() -> None:
    paths = _mounted_paths()
    # The models whose indexes post-date their data — the ones an operator
    # actually backfills. #263 added the `provenance` indexes on doc-chunk and
    # the `path` index on source-doc, both backfilled this way (no re-parse).
    assert "/source-doc/migrate/execute" in paths
    assert "/citation-event/migrate/execute" in paths
    assert "/doc-chunk/migrate/execute" in paths


def test_migrate_template_is_global() -> None:
    # Registered globally (user-approved), so it's not special-cased to the KB
    # models — every model exposes the backfill route, including a per-App
    # WorkItem model like RcaInvestigation.
    paths = _mounted_paths()
    assert "/rca-investigation/migrate/execute" in paths
