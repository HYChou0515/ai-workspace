"""Coverage fill for resources/__init__: the no-op reindex schema step.

`_reindex_only` is the identity transform wired as `step(None, _reindex_only,
…)` so migrating a pre-Schema (version `None`) row re-extracts its indexed_data
without altering the record. Here we call it directly to pin that identity.
"""

from __future__ import annotations

import workspace_app.resources as resources_pkg


def test_reindex_only_is_the_identity_transform():
    """The migration step returns the record unchanged (resources/__init__
    line 56) — the reindex is the write-back side effect, not a data change."""
    record = {"any": "record", "n": 1}
    assert resources_pkg._reindex_only(record) is record

    sentinel = object()
    assert resources_pkg._reindex_only(sentinel) is sentinel
