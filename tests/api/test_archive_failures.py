"""docs/plan-archive-full.md P2: an item whose write-back the archive refused
is recorded as such, for every pod to read, until a write-back lands.

The record is what P3's turn gate reads. It has to be shared because the pod
that ran the failing checkpoint is rarely the one a person's next message lands
on. One writer: `InvestigationRegistry._writeback` — every checkpoint, turn-end
reconcile and reap passes through it."""

from __future__ import annotations

import contextlib

from workspace_app.api.archive_health import (
    SpecstarArchiveFailures,
    register_archive_health,
)
from workspace_app.api.registry import InvestigationRegistry
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ArchiveWriteFailed, SandboxHandle, SandboxSpec

_ENOSPC = 'rsync: [receiver] write failed on "/a/f": No space left on device (28)'


class _ArchiveSandbox(MockSandbox):
    """A sandbox whose host writes back to an archive that can be made to refuse."""

    def __init__(self) -> None:
        super().__init__()
        self.refuse: str = ""
        self.persisted: list[bool] = []

    async def persist(self, handle: SandboxHandle, *, delete: bool, pack: bool = False) -> None:
        if self.refuse:
            raise ArchiveWriteFailed(self.refuse)
        self.persisted.append(delete)


def _store() -> SpecstarArchiveFailures:
    spec = make_spec(default_user="u")
    register_archive_health(spec)
    return SpecstarArchiveFailures(spec)


async def _registry(
    store: SpecstarArchiveFailures,
) -> tuple[InvestigationRegistry, _ArchiveSandbox]:
    sandbox = _ArchiveSandbox()
    registry = InvestigationRegistry(
        sandbox=sandbox, host_managed_durable=True, archive_failures=store
    )
    await registry.ensure_handle(await registry.session("item-1"))
    return registry, sandbox


async def test_a_refused_checkpoint_is_recorded_with_its_reason():
    store = _store()
    registry, sandbox = await _registry(store)
    sandbox.refuse = _ENOSPC

    assert await registry.mirror_warm() == []  # the sweep skips it, as before

    assert await store.reason_of("item-1") == _ENOSPC


async def test_a_refused_turn_end_write_back_is_recorded_too():
    store = _store()
    registry, sandbox = await _registry(store)
    sandbox.refuse = _ENOSPC

    with contextlib.suppress(ArchiveWriteFailed):  # the caller sees it, as before
        await registry.flush("item-1")

    assert await store.reason_of("item-1") == _ENOSPC


async def test_a_write_back_that_lands_clears_the_record():
    store = _store()
    registry, sandbox = await _registry(store)
    sandbox.refuse = _ENOSPC
    await registry.mirror_warm()

    sandbox.refuse = ""
    await registry.mirror_warm()

    assert await store.reason_of("item-1") is None


async def test_another_pods_record_is_settled_by_the_recheck():
    """Pod A's checkpoint failed and recorded it; pod B serves the item and its
    write-back lands. B's routine checkpoints do not read the shared store (that
    would be a store call per item every few seconds on every pod); the record is
    settled when it matters — the gate's recheck before a turn would be refused."""
    store = _store()
    await store.record("item-1", _ENOSPC)  # pod A
    registry, _ = await _registry(store)  # pod B, same shared store

    await registry.recheck_archive("item-1")

    assert await store.reason_of("item-1") is None


class _CountingStore(SpecstarArchiveFailures):
    def __init__(self, spec) -> None:  # noqa: ANN001
        super().__init__(spec)
        self.calls = 0

    async def record(self, item_id: str, reason: str) -> None:
        self.calls += 1
        await super().record(item_id, reason)

    async def clear(self, item_id: str) -> None:
        self.calls += 1
        await super().clear(item_id)


async def test_a_write_back_that_was_never_refused_costs_no_store_call():
    """Review round 1: every successful checkpoint (every few seconds, every warm
    item, every pod) used to try a delete on the shared store."""
    spec = make_spec(default_user="u")
    register_archive_health(spec)
    store = _CountingStore(spec)
    registry, _ = await _registry(store)

    await registry.mirror_warm()
    await registry.mirror_warm()

    assert store.calls == 0


class _BrokenStore(SpecstarArchiveFailures):
    async def record(self, item_id: str, reason: str) -> None:
        raise RuntimeError("store down")

    async def clear(self, item_id: str) -> None:
        raise RuntimeError("store down")


async def test_a_store_that_errors_does_not_change_what_the_write_back_reports():
    """The write-back's own outcome is what callers act on (`close_all` keeps a
    sandbox whose write-back failed): a store error must not stand in for it."""
    spec = make_spec(default_user="u")
    register_archive_health(spec)
    registry, sandbox = await _registry(_BrokenStore(spec))
    handle = (await registry.session("item-1")).handle
    assert handle is not None

    await registry._writeback("item-1", handle, delete=False)  # lands: no raise
    sandbox.refuse = _ENOSPC
    with contextlib.suppress(ArchiveWriteFailed):
        await registry._writeback("item-1", handle, delete=False)
        raise AssertionError("the refusal must surface as ArchiveWriteFailed")


async def test_a_recheck_that_hangs_gives_up_and_keeps_the_record():
    """A hung archive mount must not hang the send that asked: the recheck is
    bounded, and not knowing is not 'saved'."""
    import asyncio

    store = _store()
    registry, sandbox = await _registry(store)
    registry.archive_recheck_timeout_s = 0.1
    await store.record("item-1", _ENOSPC)
    hang = asyncio.Event()

    async def hangs(handle: SandboxHandle, *, delete: bool, pack: bool = False) -> None:
        await hang.wait()

    sandbox.persist = hangs  # ty: ignore[invalid-assignment]
    await asyncio.wait_for(registry.recheck_archive("item-1"), 2)

    assert await store.reason_of("item-1") == _ENOSPC


async def test_the_record_comes_back_after_it_was_cleared():
    store = _store()
    await store.record("item-1", "first")
    await store.clear("item-1")
    await store.record("item-1", "second")

    assert await store.reason_of("item-1") == "second"
    assert await store.reason_of("item-2") is None


# Review round 1 (D-3): the recheck must ask where the item's sandbox REALLY is.
# This pod's own session can hold a handle the host already reaped while
# another pod rebuilt the sandbox under the shared address; "my handle is gone"
# is not "nothing is live".


class _Address:
    """`IAddressStore.get` only — the recheck reads nothing else."""

    def __init__(self, handle: SandboxHandle | None) -> None:
        self.handle = handle

    async def get(self, item_id: str) -> SandboxHandle | None:
        return self.handle


class _TwoSandboxes(MockSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.refuse: dict[str, str] = {}
        self.written: list[str] = []

    async def persist(self, handle: SandboxHandle, *, delete: bool, pack: bool = False) -> None:
        self._require(handle)  # a reaped handle is SandboxNotFound, as on the host
        if handle.id in self.refuse:
            raise ArchiveWriteFailed(self.refuse[handle.id])
        self.written.append(handle.id)


async def test_a_stale_session_handle_does_not_clear_a_live_sandboxs_refusal():
    store = _store()
    sandbox = _TwoSandboxes()
    live = await sandbox.create(SandboxSpec())  # rebuilt by another pod
    sandbox.refuse[live.id] = _ENOSPC
    registry = InvestigationRegistry(
        sandbox=sandbox,
        host_managed_durable=True,
        archive_failures=store,
        address=_Address(live),  # ty: ignore[invalid-argument-type]
    )
    session = await registry.session("item-1")
    session.handle = SandboxHandle(id="reaped-long-ago")  # this pod's stale view
    await store.record("item-1", _ENOSPC)

    await registry.recheck_archive("item-1")

    assert await store.reason_of("item-1") == _ENOSPC


async def test_a_recheck_that_lands_on_the_live_sandbox_clears_it():
    store = _store()
    sandbox = _TwoSandboxes()
    live = await sandbox.create(SandboxSpec())
    registry = InvestigationRegistry(
        sandbox=sandbox,
        host_managed_durable=True,
        archive_failures=store,
        address=_Address(live),  # ty: ignore[invalid-argument-type]
    )
    session = await registry.session("item-1")
    session.handle = SandboxHandle(id="reaped-long-ago")
    await store.record("item-1", _ENOSPC)

    await registry.recheck_archive("item-1")

    assert sandbox.written == [live.id]  # cleared because it LANDED, not as moot
    assert await store.reason_of("item-1") is None
