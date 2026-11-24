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
from workspace_app.sandbox.protocol import ArchiveWriteFailed, SandboxHandle

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


async def test_another_pods_record_is_cleared_by_this_pods_success():
    """Pod A's checkpoint failed and recorded it; pod B serves the item and its
    write-back lands. The record is the item's, not the pod's."""
    store = _store()
    await store.record("item-1", _ENOSPC)  # pod A
    registry, _ = await _registry(store)  # pod B, same shared store

    await registry.mirror_warm()

    assert await store.reason_of("item-1") is None


async def test_the_record_comes_back_after_it_was_cleared():
    store = _store()
    await store.record("item-1", "first")
    await store.clear("item-1")
    await store.record("item-1", "second")

    assert await store.reason_of("item-1") == "second"
    assert await store.reason_of("item-2") is None
