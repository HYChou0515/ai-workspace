"""Per-item sandbox address (handle) shared across pods (#366).

The http sandbox-host mints an EPHEMERAL handle per ``create`` (uuid-keyed, and
it ignores the ``sandbox_id`` hint). If each API pod kept its own handle in
memory, two pods serving the same item would each ``create`` their own sandbox →
two diverging working dirs. So the *address* lives in the shared backend
(specstar), keyed by item: the first pod to claim an item's address wins and
every other pod converges on it, so an item has exactly ONE live sandbox. When
the sandbox behind an address dies, a pod swaps a fresh address in (registry
self-heal, #366 P2). The model self-registers (like the #345 heartbeat) so the
memory-default app doesn't emit its CRUD routes.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib

from msgspec import Struct
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from ..sandbox.protocol import SandboxHandle

# Real contention is a handful of pods racing one item's address for a few
# microseconds when its sandbox dies, so a generous cap is only brushed under
# pathological churn (mirrors SpecstarTurnControl's epoch CAS).
_MAX_CAS_RETRIES = 100


class IAddressStore(abc.ABC):
    """Per-item sandbox address (handle) shared across pods."""

    @abc.abstractmethod
    async def get(self, item_id: str) -> SandboxHandle | None:
        """The item's current address, or None when unclaimed."""

    @abc.abstractmethod
    async def claim(self, item_id: str, handle: SandboxHandle) -> SandboxHandle:
        """Store ``handle`` as the item's address iff none is set; return the
        EFFECTIVE address — the existing one when another pod already claimed it
        (so callers converge on ONE sandbox), else ``handle``."""

    @abc.abstractmethod
    async def swap(
        self, item_id: str, expected: SandboxHandle, new: SandboxHandle
    ) -> SandboxHandle:
        """CAS-replace the address: set it to ``new`` only if it currently equals
        ``expected`` (the address a pod found dead). Return the EFFECTIVE address —
        ``new`` when we won, else whatever a peer already swapped in (so the loser
        converges instead of forcing its own rebuild)."""

    @abc.abstractmethod
    async def forget(self, item_id: str) -> None:
        """Release the item's address slot (its sandbox was torn down / closed),
        so the next freshly-created sandbox can claim it. Idempotent."""


class _SandboxAddress(Struct):
    """One item's current sandbox address. resource_id == item_id, so every pod
    upserts/reads the one shared row by a point key (no scan)."""

    item_id: str
    handle_id: str


def register_sandbox_address(spec: SpecStar) -> None:
    """Idempotently register the address model. Safe to call on every pod."""
    with contextlib.suppress(ValueError):
        spec.add_model(_SandboxAddress)


class SpecstarAddressStore(IAddressStore):
    """``IAddressStore`` over a shared specstar backend. Blocking specstar I/O is
    offloaded to a thread so it never sits on the event loop, mirroring the rest
    of the app's specstar access."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    async def get(self, item_id: str) -> SandboxHandle | None:
        return await asyncio.to_thread(self._get_sync, item_id)

    def _get_sync(self, item_id: str) -> SandboxHandle | None:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        try:
            res = rm.get(item_id)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None  # unclaimed OR forgotten → no address
        data = res.data
        assert isinstance(data, _SandboxAddress)
        return SandboxHandle(id=data.handle_id)

    async def claim(self, item_id: str, handle: SandboxHandle) -> SandboxHandle:
        return await asyncio.to_thread(self._claim_sync, item_id, handle)

    def _claim_sync(self, item_id: str, handle: SandboxHandle) -> SandboxHandle:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        rec = _SandboxAddress(item_id=item_id, handle_id=handle.id)
        try:
            # Atomic first-writer-wins: `if_not_exists` makes concurrent claimers
            # race for the one slot; the loser gets DuplicateResourceError and
            # converges on the winner's address (so an item has ONE sandbox).
            rm.create(rec, resource_id=item_id, if_not_exists=True)  # ty: ignore[unknown-argument]
            return handle  # we won the claim (slot was empty)
        except DuplicateResourceError:
            pass  # a row exists — a live winner to converge on, or a released slot
        try:
            res = rm.get(item_id)
        except ResourceIsDeletedError:
            # forget()-released slot (tombstone) → reclaim it for our fresh sandbox
            rm.restore(item_id)
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return handle
        data = res.data
        assert isinstance(data, _SandboxAddress)
        return SandboxHandle(id=data.handle_id)  # live → converge on the winner

    async def swap(
        self, item_id: str, expected: SandboxHandle, new: SandboxHandle
    ) -> SandboxHandle:
        return await asyncio.to_thread(self._swap_sync, item_id, expected, new)

    def _swap_sync(
        self, item_id: str, expected: SandboxHandle, new: SandboxHandle
    ) -> SandboxHandle:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = rm.get(item_id)
            except (ResourceIDNotFoundError, ResourceIsDeletedError):
                return self._claim_sync(item_id, new)  # slot freed mid-flight → claim fresh
            data = res.data
            assert isinstance(data, _SandboxAddress)
            current = SandboxHandle(id=data.handle_id)
            if current != expected:
                return current  # a peer already refreshed → converge on theirs
            try:
                rm.modify(
                    item_id,
                    _SandboxAddress(item_id=item_id, handle_id=new.id),
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                return new  # we won the swap
            except PreconditionFailedError:  # pragma: no cover - cross-pod CAS race
                continue  # a peer modified between our get and modify → re-read
        raise RuntimeError(  # pragma: no cover - only under pathological churn
            f"address swap CAS exhausted retries for {item_id!r}"
        )

    async def forget(self, item_id: str) -> None:
        await asyncio.to_thread(self._forget_sync, item_id)

    def _forget_sync(self, item_id: str) -> None:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            rm.delete(item_id)
