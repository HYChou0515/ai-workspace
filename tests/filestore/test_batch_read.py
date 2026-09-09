"""The durable store's batch read (#781 Phase 4).

A COLD workspace — one with no live sandbox — is answered by the durable store,
and reading a listing there was one row fetch per file. The sandbox fast lane
does nothing for it: the round trips are to the database.

Same contract as every other batch here: same bytes, same tolerance of a path
that is not there, and no caller told which way it was answered.
"""

from __future__ import annotations

from typing import cast

import pytest

from workspace_app.files.facade import WorkspaceFiles, read_all, read_all_existing
from workspace_app.filestore.protocol import FileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore


async def _seeded(store: SpecstarFileStore, count: int) -> None:
    for i in range(count):
        await store.write("ws1", f"/r/{i}.md", f"body {i}".encode())


async def test_reading_a_listing_costs_one_query_not_one_fetch_per_file(
    store: SpecstarFileStore, monkeypatch
) -> None:
    """What the batch is for. Counting the store's own row fetches is the honest
    measure: on the in-memory backend they are free, and in production each one
    is a database round trip."""
    await _seeded(store, 20)
    fetches = 0
    original = store._files.get

    def counted(*a, **k):
        nonlocal fetches
        fetches += 1
        return original(*a, **k)

    monkeypatch.setattr(store._files, "get", counted)
    got = await store.read_many_existing("ws1", [f"/r/{i}.md" for i in range(20)])

    assert got == {f"/r/{i}.md": f"body {i}".encode() for i in range(20)}
    assert fetches == 0, f"{fetches} single-row fetches — the batch read is not being used"


class _NoBatchStore:
    """The same durable store with the cold lane CLOSED — every assertion below
    has to hold on this one too, or the lane is observable from outside.

    A plain wrapper rather than a subclass: the batch method has to be genuinely
    absent for the facade's duck-type to miss it, and an override that raises
    would still be found."""

    def __init__(self, inner: SpecstarFileStore) -> None:
        self._inner = inner

    #: Every name the facade duck-types to find a batch. Hiding only some of
    #: them is how "lane closed" quietly became "lane open": the code started
    #: looking for `read_many_existing` first, the double still forwarded it, and
    #: both halves of every pair asserted the SAME path while claiming not to.
    _BATCH_NAMES = ("read_many", "read_many_existing")

    def __getattr__(self, name: str):
        if name in self._BATCH_NAMES:
            raise AttributeError(name)
        return getattr(self._inner, name)


def _closed(store: SpecstarFileStore) -> FileStore:
    """The same store with the cold lane shut. `cast` rather than a subclass:
    the point is that the facade's duck-type finds NOTHING, which an override
    could not express.

    `test_the_closed_lane_is_really_closed` is its positive control, and it is
    stated in terms of BEHAVIOUR (per-path reads actually happen) rather than
    the name list — a control that checks the double against its own list of
    names to hide cannot notice that list being wrong, which is the failure it
    exists to catch."""
    return cast(FileStore, _NoBatchStore(store))


async def test_the_closed_lane_is_really_closed(store: SpecstarFileStore, monkeypatch) -> None:
    """The double is only worth something if the facade genuinely fails to find a
    batch on it — otherwise every "lane-closed" case below runs the open lane and
    passes anyway. That already happened once: the code grew a second capability
    name, the double kept forwarding it, and the pairs silently became one path
    asserted twice.

    Three paths must therefore cost three per-path reads. A name-list assertion
    would not do: it would be checking the double against the very list that was
    wrong."""
    await _seeded(store, 3)
    singles = 0
    original = store.read

    async def counted(workspace_id: str, path: str):
        nonlocal singles
        singles += 1
        return await original(workspace_id, path)

    monkeypatch.setattr(store, "read", counted)

    got = await read_all(WorkspaceFiles(_closed(store)), "ws1", ["/r/0.md", "/r/1.md", "/r/2.md"])

    assert len(got) == 3
    assert singles == 3, f"{singles} per-path reads for 3 paths — the lane is not closed"


async def test_the_query_bounds_its_own_size(store: SpecstarFileStore, monkeypatch) -> None:
    """The predicate becomes one SQL `IN (...)`, so THIS is where the ask has to
    be bounded — `kb/graph/link.py` caps its own at 500 after a 40,000-id one
    built a 937 KB statement the database refused.

    It is bounded here rather than in the caller because a caller that chunks
    calls the store once per chunk, and through the facade that resolved the
    workspace's liveness once per chunk too."""
    for i in range(450):
        await store.write("ws1", f"/r/{i}.md", b"x")
    asks: list[int] = []
    original = store._read_many_sync

    def counted(workspace_id: str, paths: list[str]):
        asks.append(len(paths))
        return original(workspace_id, paths)

    monkeypatch.setattr(store, "_read_many_sync", counted)

    got = await store.read_many_existing("ws1", [f"/r/{i}.md" for i in range(450)])

    assert len(got) == 450
    assert asks and max(asks) <= 200, f"largest query carried {max(asks)} paths"


@pytest.mark.parametrize("closed", [False, True], ids=["lane-open", "lane-closed"])
async def test_the_batch_answers_in_the_order_asked(store: SpecstarFileStore, closed: bool) -> None:
    """A query answers in whatever order the backend likes; the result is
    re-ordered against the paths asked for. True on both lanes — the per-path
    loop is trivially in order, so only the batch could lose it."""
    await _seeded(store, 5)
    files = WorkspaceFiles(_closed(store) if closed else store)

    got = await read_all(files, "ws1", ["/r/3.md", "/r/0.md", "/r/4.md"])

    assert got == [b"body 3", b"body 0", b"body 4"]


@pytest.mark.parametrize("closed", [False, True], ids=["lane-open", "lane-closed"])
async def test_one_workspace_cannot_read_another_ones_file_either_way(
    store: SpecstarFileStore, closed: bool
) -> None:
    """Scoping is not a property of the fast lane. A batch that matched on path
    alone would hand one item's records to another item holding the same name —
    and the per-path lane, which keys by workspace, would not."""
    import pytest as _pytest

    from workspace_app.filestore.protocol import FileNotFound

    await store.write("ws1", "/r/secret.md", b"mine")
    files = WorkspaceFiles(_closed(store) if closed else store)

    with _pytest.raises(FileNotFound):
        await read_all(files, "ws2", ["/r/secret.md"])


async def test_a_path_that_is_not_there_keeps_the_two_existing_behaviours(
    store: SpecstarFileStore,
) -> None:
    """The strict read still raises and the tolerant one still skips — the pair
    that batching is most likely to quietly break."""
    import pytest

    from workspace_app.filestore.protocol import FileNotFound

    await _seeded(store, 3)
    files = WorkspaceFiles(store)

    with pytest.raises(FileNotFound):
        await read_all(files, "ws1", ["/r/0.md", "/r/gone.md"])

    kept = await read_all_existing(files, "ws1", ["/r/0.md", "/r/gone.md", "/r/1.md"])
    assert kept == {"/r/0.md": b"body 0", "/r/1.md": b"body 1"}


async def test_one_workspace_cannot_read_another_ones_file(store: SpecstarFileStore) -> None:
    """The batch is scoped to the workspace like every other read here. A query
    that matched on path alone would hand one item's records to another."""
    await store.write("ws1", "/r/secret.md", b"mine")

    assert await store.read_many_existing("ws2", ["/r/secret.md"]) == {}


async def test_a_vanished_file_does_not_re_fetch_the_cold_listing(
    store: SpecstarFileStore, monkeypatch
) -> None:
    """The cold lane's own guard, which it did not have.

    A mutation check showed the whole cold lane could be reverted to the old
    strict-batch-plus-repair shape with every test still green — the lenient
    contract was asserted nowhere. Its point is that a miss costs the miss: one
    query, no per-path re-read, whatever is gone simply absent."""
    await _seeded(store, 10)
    singles = 0
    original = store.read

    async def counted(workspace_id: str, path: str):
        nonlocal singles
        singles += 1
        return await original(workspace_id, path)

    monkeypatch.setattr(store, "read", counted)
    files = WorkspaceFiles(store)

    kept = await read_all_existing(files, "ws1", [f"/r/{i}.md" for i in range(10)] + ["/r/gone.md"])

    assert len(kept) == 10
    assert singles == 0, f"{singles} per-path reads — the miss re-fetched the listing"
