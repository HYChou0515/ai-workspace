"""#390 cross-path index-result cache — the composite key.

Re-indexing content that was already indexed under the same settings should
copy the stored chunks instead of re-parsing + re-embedding. The reuse is keyed
on the three things that fully determine a doc's chunks + vectors:

    content bytes (``SourceDoc.content.file_id``)
  + effective extraction prompt (guidance string + merged parser configs)
  + embedder identity (model + doc prefix — see ``Embedder.identity``)

Deliberately NOT in the key: the collection id (the same bytes anywhere reuse
one entry — that's the whole point), the file extension (rare same-bytes /
different-extension collision is handled by a manual reindex), and any global
"system version" (a parser/pipeline change is a manual-reindex event, which
invalidates the entry). See issue #390's grill for the rationale.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from typing import Any

import xxhash
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources.kb import IndexCache


def compute_cache_key(
    *,
    content_file_id: str,
    guidance: str,
    configs: Mapping[str, Mapping[str, Any]],
    embedder_identity: str,
) -> str:
    """The content-addressed :class:`~workspace_app.resources.kb.IndexCache`
    resource id for these inputs — a deterministic, slash-free hash (a specstar
    id can't contain ``/``).

    ``configs`` is the merged ``parser_id -> {knob: value}`` map (collection
    overlaid by per-doc override); it is serialised with sorted keys so dict
    ordering never splits one logical setting into two entries.
    """
    payload = json.dumps(
        {"c": content_file_id, "g": guidance, "cfg": configs, "e": embedder_identity},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return xxhash.xxh3_128_hexdigest(payload.encode())


class IndexCacheStore:
    """Point get / put / delete for :class:`IndexCache` rows, keyed by the
    composite id from :func:`compute_cache_key`. A thin seam so the cache's
    persistence lives in one place; the Ingestor orchestrates on top of it."""

    def __init__(self, spec: SpecStar) -> None:
        self._rm = spec.get_resource_manager(IndexCache)

    def get(self, key: str) -> IndexCache | None:
        """The cached entry, or ``None`` on a miss."""
        try:
            entry = self._rm.get(key).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(entry, IndexCache)
        return entry

    def put(self, key: str, entry: IndexCache) -> None:
        self._rm.create_or_update(key, entry)

    def delete(self, key: str) -> None:
        """Drop the entry if present (idempotent — a miss is a no-op)."""
        with contextlib.suppress(ResourceIDNotFoundError):
            self._rm.permanently_delete(key)
