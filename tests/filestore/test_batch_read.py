"""The durable store's batch read (#781 Phase 4).

A COLD workspace — one with no live sandbox — is answered by the durable store,
and reading a listing there was one row fetch per file. The sandbox fast lane
does nothing for it: the round trips are to the database.

Same contract as every other batch here: same bytes, same tolerance of a path
that is not there, and no caller told which way it was answered.
"""

from __future__ import annotations

from workspace_app.files.facade import WorkspaceFiles, read_all, read_all_existing
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
    got = await store.read_many("ws1", [f"/r/{i}.md" for i in range(20)])

    assert got == [f"body {i}".encode() for i in range(20)]
    assert fetches == 0, f"{fetches} single-row fetches — the batch read is not being used"


async def test_the_batch_answers_in_the_order_asked(store: SpecstarFileStore) -> None:
    """A query answers in whatever order the backend likes; the caller zips the
    result against the paths it asked for, so the order has to be restored."""
    await _seeded(store, 5)

    got = await store.read_many("ws1", ["/r/3.md", "/r/0.md", "/r/4.md"])

    assert got == [b"body 3", b"body 0", b"body 4"]


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
    import pytest

    from workspace_app.filestore.protocol import FileNotFound

    await store.write("ws1", "/r/secret.md", b"mine")

    with pytest.raises(FileNotFound):
        await store.read_many("ws2", ["/r/secret.md"])
