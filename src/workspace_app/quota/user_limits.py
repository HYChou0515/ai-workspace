"""Per-person limits, in two layers: a deploy default and an optional override.

A single number for everyone cannot express "this person is an exception", and
exceptions are certain — somebody will need to run something large. So the read
path is `override ◇ deploy default`, with only the second layer populated until
an operator sets the first.

Resolved per CHECK rather than captured at boot, so raising someone's allowance
takes effect within seconds instead of at the next restart. The read is memoised
for `ttl_s`; a write clears the memo on the pod that served it, so the bound is
"immediate there, at most `ttl_s` on the other replicas" — there is no
cross-pod invalidation, and claiming instant would be claiming something this
deployment shape cannot deliver.

An override is per DIMENSION: setting only `count` leaves cpu/memory/disk on the
deploy default, the same fall-through the per-App limits use, so an exception
grants exactly what it says and nothing else.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from msgspec import Struct
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from ..config.schema import PerUserResources

logger = logging.getLogger(__name__)

# Matches the item-facts memo in `create_app` and the usage measurement window.
_TTL_S = 5.0
_CACHE_MAX = 4096


class _UserQuota(Struct):
    """One person's overrides. resource_id == user id, so a lookup is a point
    read. Every field is optional in the "0 / empty means not stated" sense this
    feature uses everywhere — an override says only what it changes."""

    user_id: str
    count: int = 0
    cpu: float = 0.0
    memory: str = ""
    disk: str = ""


def register_user_quota(spec: SpecStar) -> None:
    """Idempotently register the override model, post-`spec.apply`. Its CRUD
    routes stay unemitted: writing an allowance is an admin action with its own
    guarded route, not something any authenticated caller may PUT."""
    with contextlib.suppress(ValueError):
        spec.add_model(_UserQuota)


class UserLimits:
    """Resolves one person's effective limits, and records overrides."""

    def __init__(self, spec: SpecStar, default: PerUserResources, *, ttl_s: float = _TTL_S) -> None:
        self._spec = spec
        self._default = default
        # Overrides are rare and change by an admin action, but this is read on
        # a path that runs per gated write. Memoised for the same window the
        # usage measurement already trails by, so an allowance is never more
        # stale than the numbers it is compared against.
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, PerUserResources]] = {}

    @property
    def default(self) -> PerUserResources:
        return self._default

    async def for_user(self, user_id: str) -> PerUserResources:
        now = time.monotonic()
        hit = self._cache.get(user_id)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        resolved = await self._resolve(user_id)
        if len(self._cache) > _CACHE_MAX:  # bounded: a cache, not a registry
            self._cache.clear()
        self._cache[user_id] = (now, resolved)
        return resolved

    async def _resolve(self, user_id: str) -> PerUserResources:
        override = await asyncio.to_thread(self._read_sync, user_id)
        if override is None:
            return self._default
        d = self._default
        return PerUserResources(
            count=override.count or d.count,
            cpu=override.cpu or d.cpu,
            memory=override.memory or d.memory,
            disk=override.disk or d.disk,
        )

    def _read_sync(self, user_id: str) -> _UserQuota | None:
        rm = self._spec.get_resource_manager(_UserQuota)
        try:
            res = rm.get(user_id)
        except (ResourceIDNotFoundError, ResourceIsDeletedError, KeyError):
            return None
        data = res.data
        assert isinstance(data, _UserQuota)
        return data

    async def set_for(self, user_id: str, limits: PerUserResources) -> None:
        await asyncio.to_thread(self._set_sync, user_id, limits)
        # Drop the memo on THIS pod, so the admin who just made the change sees
        # it on the very next check. Other replicas keep their copy until the
        # TTL expires — this app is multi-replica by design (#345 / #366), and
        # there is no invalidation broadcast. So the honest guarantee is: at most
        # `ttl_s` anywhere, immediate where the change was made. Not "instant",
        # which is what a reader would otherwise take from this line.
        self._cache.pop(user_id, None)

    def _set_sync(self, user_id: str, limits: PerUserResources) -> None:
        rm = self._spec.get_resource_manager(_UserQuota)
        rec = _UserQuota(
            user_id=user_id,
            count=limits.count,
            cpu=limits.cpu,
            memory=limits.memory,
            disk=limits.disk,
        )
        logger.info("quota: setting per-user override for %s -> %s", user_id, rec)
        try:
            rm.modify(user_id, rec, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            pass
        except ResourceIsDeletedError:
            rm.restore(user_id)
            rm.modify(user_id, rec, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(rec, resource_id=user_id, status=RevisionStatus.draft)

    async def clear_for(self, user_id: str) -> None:
        """Drop an override so the person falls back to the deploy default."""
        await asyncio.to_thread(self._clear_sync, user_id)
        self._cache.pop(user_id, None)

    def _clear_sync(self, user_id: str) -> None:
        rm = self._spec.get_resource_manager(_UserQuota)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError, KeyError):
            rm.delete(user_id)
