"""#633 — build the name index once, reuse it, and degrade safely.

Building means reading every identity's names: the one expensive step in this
design (seconds at 40k rows). Per message that would cost more than the problem
it solves, so it is built once and reused with a TTL.

**A stale index is safe**, and that is the whole reason a TTL is enough here. A
name it has not learned yet simply is not auto-injected, and the agent's
`lookup_entity` tool still finds it by querying the database. Nothing becomes
wrong; one convenience is briefly missing. That is also why there is no
cross-pod invalidation: the thing being coordinated does not need to be correct
everywhere at once.

A rebuild that FAILS keeps serving the previous index rather than raising —
losing auto-injection is a degradation, taking the turn down with it is an
outage. With nothing cached yet, an empty index is served, which is exactly the
behaviour that existed before this feature.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .name_index import NameIndex

_LOGGER = logging.getLogger(__name__)

# Long enough that rebuilds are rare, short enough that a freshly indexed deck
# starts being offered within one coffee break.
DEFAULT_TTL_S = 300.0


class NameIndexCache:
    """A process-local :class:`NameIndex`, rebuilt at most every ``ttl_s``."""

    __slots__ = ("_built_at", "_index", "_load", "_now", "_ttl")

    def __init__(
        self,
        load: Callable[[], dict[str, tuple[str, ...]]],
        *,
        ttl_s: float = DEFAULT_TTL_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._load = load
        self._ttl = ttl_s
        self._now = now
        self._index: NameIndex | None = None
        self._built_at = 0.0

    def get(self) -> NameIndex:
        """The current index, rebuilding it if the TTL has passed."""
        now = self._now()
        if self._index is not None and now - self._built_at < self._ttl:
            return self._index
        try:
            self._index = NameIndex(self._load())
            self._built_at = now
        except Exception:  # pragma: no cover - exercised via the failing-load test
            if self._index is None:
                _LOGGER.warning("graph: name index unavailable; auto-injection is off")
                self._index = NameIndex({})
                self._built_at = now
            else:
                # Keep serving what we have; retry on the next expiry rather than
                # hammering a struggling database once per message.
                _LOGGER.warning("graph: name index rebuild failed; serving the previous one")
                self._built_at = now
        return self._index


# One index per spec per process. Two routers need it — the chat turn that
# injects (#633) and the browser that searches (#636) — and neither owns it, so
# it lives here rather than as a private helper in whichever imported it first.
_INDEXES: dict[int, NameIndexCache] = {}


def load_graph_names(spec: object) -> dict[str, tuple[str, ...]]:
    """Every identity's names → the ids they resolve to.

    Read WITHOUT an access scope on purpose: this is a name→id map, and every
    use re-reads the identity AS the caller before showing anything. Filtering
    here instead would mean one index per user.
    """
    from ...resources.graph import GraphEntity

    names: dict[str, tuple[str, ...]] = {}
    rm = spec.get_resource_manager(GraphEntity)  # ty: ignore[unresolved-attribute]
    for r in rm.list_resources():
        entity = r.data
        if not isinstance(entity, GraphEntity) or entity.merged_into or not entity.collection_ids:
            continue  # a tombstone, or an identity nothing vouches for
        rid = r.info.resource_id
        for key in entity.norm_keys:
            names[key] = names.get(key, ()) + (rid,)
    return names


def graph_name_index(spec: object) -> NameIndexCache:
    """The process-local index for ``spec``, built on first use."""
    cache = _INDEXES.get(id(spec))
    if cache is None:
        cache = NameIndexCache(lambda: load_graph_names(spec))
        _INDEXES[id(spec)] = cache
    return cache
