"""Specstar-backed ITurnControl — the cross-pod cancel epoch as a resource.

Each turn-engine key gets one `TurnEpoch` row whose specstar id IS the key (an
opaque, slash-free specstar id already — an item id or chat id), so every pod
over the shared backend reads and bumps the SAME counter. That is the whole
point: a Stop / new message handled on one replica must reach a turn running on
another, and the only thing both replicas share is the specstar store.

`advance` is an optimistic create-or-increment with CAS retry (a peer pod may
bump first). Increments use `modify` (draft revisions) — like specstar's own
`HeartbeatThread` — so this hot, per-turn field doesn't pile up a stable
revision per turn. specstar calls are synchronous, so they run off the event
loop via `asyncio.to_thread`; the watcher's `current` poll must never block the
loop serving every other turn.
"""

from __future__ import annotations

import asyncio
import contextlib

from msgspec import Struct
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    RevisionStatus,
)

from .base import ITurnControl

# Epoch advance touches a single tiny field; real contention is a handful of
# pods racing the same key for a few microseconds, so a generous cap is still
# only ever brushed under pathological churn.
_MAX_CAS_RETRIES = 100


class TurnEpoch(Struct):  # → resource "turn-epoch"
    epoch: int


class SpecstarTurnControl(ITurnControl):
    def __init__(self, spec: SpecStar) -> None:
        # add_model is one-shot per spec; tolerate a second control over the same
        # spec (the epochs persist in the spec's storage either way).
        with contextlib.suppress(ValueError):
            spec.add_model(TurnEpoch)
        self._rm = spec.get_resource_manager(TurnEpoch)

    async def current(self, key: str) -> int:
        return await asyncio.to_thread(self._current_sync, key)

    async def advance(self, key: str) -> int:
        return await asyncio.to_thread(self._advance_sync, key)

    def _current_sync(self, key: str) -> int:
        try:
            data = self._rm.get(key).data
        except ResourceIDNotFoundError:
            return 0  # no turn has ever started for this key
        assert isinstance(data, TurnEpoch)  # narrow for ty (coverage-clean)
        return data.epoch

    def _advance_sync(self, key: str) -> int:
        """Atomically create-or-increment the key's epoch, retrying when a peer
        pod wins the race (CAS / first-create-wins)."""
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = self._rm.get(key)
            except ResourceIDNotFoundError:
                try:
                    self._rm.create(
                        TurnEpoch(epoch=1),
                        resource_id=key,
                        if_not_exists=True,  # ty: ignore[unknown-argument]
                    )
                    return 1
                except DuplicateResourceError:  # pragma: no cover — cross-pod create race
                    continue  # a peer created it first — re-read and increment
            else:
                data = res.data
                assert isinstance(data, TurnEpoch)  # narrow for ty (coverage-clean)
                nxt = data.epoch + 1
                try:
                    self._rm.modify(
                        key,
                        TurnEpoch(epoch=nxt),
                        status=RevisionStatus.draft,
                        expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                    )
                    return nxt
                except PreconditionFailedError:  # pragma: no cover — cross-pod CAS race
                    continue  # a peer advanced first — re-read and retry
        raise RuntimeError(f"TurnEpoch CAS exhausted retries for {key!r}")  # pragma: no cover
