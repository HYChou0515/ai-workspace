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
import logging

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
from ..tooling.external import MountedTool

# Real contention is a handful of pods racing one item's address for a few
# microseconds when its sandbox dies, so a generous cap is only brushed under
# pathological churn (mirrors SpecstarTurnControl's epoch CAS).
_MAX_CAS_RETRIES = 100

logger = logging.getLogger(__name__)


class IAddressStore(abc.ABC):
    """Per-item sandbox address (handle) shared across pods."""

    @abc.abstractmethod
    async def get(self, item_id: str) -> SandboxHandle | None:
        """The item's current address, or None when unclaimed."""

    @abc.abstractmethod
    async def claim(
        self,
        item_id: str,
        handle: SandboxHandle,
        *,
        tools: dict[str, MountedTool] | None = None,
    ) -> SandboxHandle:
        """Store ``handle`` as the item's address iff none is set; return the
        EFFECTIVE address — the existing one when another pod already claimed it
        (so callers converge on ONE sandbox), else ``handle``.

        ``tools`` is what that sandbox mounted (plan-tool-running-version), kept
        in the SAME write as the handle so a reader can never pair one sandbox's
        address with another's bundles. A losing claim writes nothing."""

    @abc.abstractmethod
    async def swap(
        self,
        item_id: str,
        expected: SandboxHandle,
        new: SandboxHandle,
        *,
        tools: dict[str, MountedTool] | None = None,
    ) -> SandboxHandle:
        """CAS-replace the address: set it to ``new`` only if it currently equals
        ``expected`` (the address a pod found dead). Return the EFFECTIVE address —
        ``new`` when we won, else whatever a peer already swapped in (so the loser
        converges instead of forcing its own rebuild). ``tools`` as in `claim`."""

    @abc.abstractmethod
    async def mounted(self, item_id: str) -> dict[str, MountedTool] | None:
        """What the sandbox at the item's address was created with. ``None`` is
        UNKNOWN — no address, or one written without the record (an older build,
        a create whose resolve failed) — and never means "mounted nothing"
        (that is ``{}``)."""

    @abc.abstractmethod
    async def forget(self, item_id: str) -> None:
        """Release the item's address slot (its sandbox was torn down / closed),
        so the next freshly-created sandbox can claim it. Idempotent."""


class _Mounted(Struct):
    sha: str
    version: str = ""


class _SandboxAddress(Struct):
    """One item's current sandbox address. resource_id == item_id, so every pod
    upserts/reads the one shared row by a point key (no scan)."""

    item_id: str
    handle_id: str
    #: plan-tool-running-version: the bundles that sandbox mounted at create.
    #: Defaulted, so a row written before the field decodes as UNKNOWN.
    tools: dict[str, _Mounted] | None = None


def _row(item_id: str, handle: SandboxHandle, tools: dict[str, MountedTool] | None):
    return _SandboxAddress(
        item_id=item_id,
        handle_id=handle.id,
        tools=None
        if tools is None
        else {n: _Mounted(sha=m.sha, version=m.version) for n, m in tools.items()},
    )


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

    async def claim(
        self,
        item_id: str,
        handle: SandboxHandle,
        *,
        tools: dict[str, MountedTool] | None = None,
    ) -> SandboxHandle:
        return await asyncio.to_thread(self._claim_sync, item_id, handle, tools)

    def _claim_sync(
        self,
        item_id: str,
        handle: SandboxHandle,
        tools: dict[str, MountedTool] | None = None,
    ) -> SandboxHandle:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        rec = _row(item_id, handle, tools)
        try:
            # Atomic first-writer-wins: `if_not_exists` makes concurrent claimers
            # race for the one slot; the loser gets DuplicateResourceError and
            # converges on the winner's address (so an item has ONE sandbox).
            rm.create(rec, resource_id=item_id, if_not_exists=True)  # ty: ignore[unknown-argument]
            logger.info("address: item %s claimed handle %s (won empty slot)", item_id, handle.id)
            return handle  # we won the claim (slot was empty)
        except DuplicateResourceError:
            logger.debug("address: item %s already has a row, resolving winner", item_id)
        try:
            res = rm.get(item_id)
        except ResourceIsDeletedError:
            logger.info(
                "address: item %s reclaiming released slot -> handle %s", item_id, handle.id
            )
            # forget()-released slot (tombstone) → reclaim it for our fresh sandbox
            rm.restore(item_id)
            rm.modify(item_id, rec, status=RevisionStatus.draft)
            return handle
        data = res.data
        assert isinstance(data, _SandboxAddress)
        logger.info(
            "address: item %s already claimed by handle %s, converging",
            item_id,
            data.handle_id,
        )
        return SandboxHandle(id=data.handle_id)  # live → converge on the winner

    async def swap(
        self,
        item_id: str,
        expected: SandboxHandle,
        new: SandboxHandle,
        *,
        tools: dict[str, MountedTool] | None = None,
    ) -> SandboxHandle:
        return await asyncio.to_thread(self._swap_sync, item_id, expected, new, tools)

    def _swap_sync(
        self,
        item_id: str,
        expected: SandboxHandle,
        new: SandboxHandle,
        tools: dict[str, MountedTool] | None = None,
    ) -> SandboxHandle:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = rm.get(item_id)
            except (ResourceIDNotFoundError, ResourceIsDeletedError):
                logger.debug("address: swap item %s slot freed, claiming fresh", item_id)
                return self._claim_sync(item_id, new, tools)  # slot freed mid-flight → claim fresh
            data = res.data
            assert isinstance(data, _SandboxAddress)
            current = SandboxHandle(id=data.handle_id)
            if current != expected:
                logger.info(
                    "address: swap item %s lost CAS -> peer address %s, converging",
                    item_id,
                    current.id,
                )
                return current  # a peer already refreshed → converge on theirs
            try:
                rm.modify(
                    item_id,
                    _row(item_id, new, tools),
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                logger.info("address: swap item %s -> handle %s (won CAS)", item_id, new.id)
                return new  # we won the swap
            except PreconditionFailedError:  # pragma: no cover - cross-pod CAS race
                continue  # a peer modified between our get and modify → re-read
        raise RuntimeError(  # pragma: no cover - only under pathological churn
            f"address swap CAS exhausted retries for {item_id!r}"
        )

    async def mounted(self, item_id: str) -> dict[str, MountedTool] | None:
        return await asyncio.to_thread(self._mounted_sync, item_id)

    def _mounted_sync(self, item_id: str) -> dict[str, MountedTool] | None:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        try:
            data = rm.get(item_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(data, _SandboxAddress)
        if data.tools is None:
            return None
        return {n: MountedTool(sha=m.sha, version=m.version) for n, m in data.tools.items()}

    async def forget(self, item_id: str) -> None:
        await asyncio.to_thread(self._forget_sync, item_id)

    def _forget_sync(self, item_id: str) -> None:
        rm = self._spec.get_resource_manager(_SandboxAddress)
        logger.debug("address: forget item %s (releasing address slot)", item_id)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            rm.delete(item_id)
