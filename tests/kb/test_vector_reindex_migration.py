"""specstar 0.12.1 stopped copying a `Vector` field into the `indexed_data`
JSONB — but only for NEW writes. Existing rows keep a fat `indexed_data` (one
float array per embedding, indexed element-by-element by the GIN) until each row
is rewritten. The only supported rewrite is the migrate route, and migrate SKIPS
any row already at the latest schema version (verified in specstar:
`migrate.py` `_migrate_single_resource` and `core.py` `migrate`). So a no-op
schema-version bump is required to give the operator's `migrate/execute`
something to act on.

This pins that both Vector-bearing models are migratable:

* `doc-chunk` carries three embeddings (`embedding`, `embedding_alt`,
  `embedding_img`) and was at `v4`; it needs a `v4 -> v5` reindex step so its
  existing rows are a version behind and get re-extracted.
* `cluster-member` carries `embedding` and had NO `Schema` at all — migrate
  raised `ValueError("Migration is not set")` for it — so it needed a `Schema`
  added before it could be cleaned.

The actual JSONB strip happens in the Postgres meta store and is covered by
specstar's own tests; here we only guarantee the app hands migrate a delta to
work with (the memory backend used in unit tests does not strip).
"""

from __future__ import annotations

import pytest

from workspace_app.resources import make_spec


def _rm(name: str):
    return make_spec().get_resource_manager(name)


def test_doc_chunk_schema_advanced_so_existing_rows_get_a_migrate_delta():
    # v4 rows (every existing chunk) must now be a version behind the latest, or
    # `migrate/execute` would report them "skipped" and never strip the vector.
    rm = _rm("doc-chunk")
    assert rm.schema_version == "v5"


def test_cluster_member_is_now_migratable():
    # It had no Schema, so migrate raised outright. A Schema with a reindex step
    # makes `migrate/execute` a working no-op transform that re-extracts.
    rm = _rm("cluster-member")
    # `schema_version` raises when no migration is configured; reaching a value
    # at all is the assertion.
    assert rm.schema_version is not None


@pytest.mark.parametrize("model", ["doc-chunk", "cluster-member"])
def test_both_vector_models_expose_the_migrate_route(model: str):
    from fastapi import FastAPI

    app = FastAPI()
    make_spec().apply(app)
    paths = {p for r in app.routes if (p := getattr(r, "path", None)) is not None}
    assert f"/{model}/migrate/execute" in paths
