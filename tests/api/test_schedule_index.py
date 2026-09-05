"""Which items have schedules — so the sweep does not read every item, ever.

Today's trigger sweep enumerates apps × profiles: a small, static set. Reading
item-level declarations changes that to "every item", and items grow without
limit. This index is the answer, and it is the same shape the platform already
uses for per-item facts it must look up without a session (`_SandboxActivity`,
`_SandboxAddress`): one opaque row per item.

It is maintained on the WRITE path, not in a route. A page writes its
`schedules.json` through the file PUT route; the agent writes the same file
through its `write_file` tool, which never touches a route. Hooking routes would
catch one and miss the other — so the hook sits at the chokepoint every write
already shares, exactly where the quota gate sits.

The index is allowed to be stale in ONE direction: it may name a file that has
since been deleted. That is why the sweep re-reads and drops what it cannot
find, and it is why deletes need no hook of their own.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar

from workspace_app.api.schedule_index import (
    SCHEDULES_FILE,
    ScheduleIndex,
    is_schedule_file,
    register_schedule_index,
)
from workspace_app.resources import make_spec


@pytest.fixture
def index() -> ScheduleIndex:
    spec: SpecStar = make_spec(default_user="alice")
    register_schedule_index(spec)
    return ScheduleIndex(spec)


# ── what counts ──────────────────────────────────────────────────────────────


def test_a_pages_schedules_file_counts():
    assert is_schedule_file(f"/scrap-review/{SCHEDULES_FILE}") is True


def test_anything_else_does_not():
    """The hook runs on EVERY write in the platform. It has to be cheap and it
    has to be certain — a loose match would index items that have no schedules
    and make the sweep read them forever."""
    for path in (
        "/scrap-review/data.json",
        "/scrap-review/schedules.json.bak",
        "/notes.md",
        f"/{SCHEDULES_FILE}",  # the workspace root is not a page's folder
    ):
        assert is_schedule_file(path) is False, path


# ── the index ────────────────────────────────────────────────────────────────


def test_records_the_item_and_the_file(index: ScheduleIndex):
    index.record("i1", f"/scrap-review/{SCHEDULES_FILE}")

    assert index.items() == ["i1"]
    assert index.paths("i1") == [f"/scrap-review/{SCHEDULES_FILE}"]


def test_writing_the_same_file_again_costs_nothing(index: ScheduleIndex):
    """A page saves on every edit, and this runs on the write path — so "already
    known" must cost a point read, not a round-trip.

    Asserted on whether it WROTE, not on the resulting path list. The list looks
    identical either way (the merge is a set), so a mutation deleting the guard
    passed every assertion this test made in its first draft. The cost was real
    and invisible, which is the only kind that survives.
    """
    wrote = [index.record("i1", f"/scrap-review/{SCHEDULES_FILE}") for _ in range(3)]

    assert wrote == [True, False, False]
    assert index.paths("i1") == [f"/scrap-review/{SCHEDULES_FILE}"]


def test_two_pages_in_one_item_are_both_recorded(index: ScheduleIndex):
    index.record("i1", f"/scrap-review/{SCHEDULES_FILE}")
    index.record("i1", f"/lot-tracker/{SCHEDULES_FILE}")

    assert sorted(index.paths("i1")) == [
        f"/lot-tracker/{SCHEDULES_FILE}",
        f"/scrap-review/{SCHEDULES_FILE}",
    ]


def test_an_item_with_nothing_recorded_is_not_listed(index: ScheduleIndex):
    """The sweep's whole point is to read a short list. An item that never wrote
    a schedule must not appear in it."""
    index.record("i1", f"/scrap-review/{SCHEDULES_FILE}")

    assert index.items() == ["i1"]
    assert index.paths("i2") == []


def test_a_path_can_be_dropped_when_its_file_is_gone(index: ScheduleIndex):
    """The index is allowed to be stale in one direction only. The sweep re-reads
    each path and drops what is no longer there, which is what makes a delete
    need no hook of its own — one fewer exit to cover, and exits are what get
    missed."""
    index.record("i1", f"/scrap-review/{SCHEDULES_FILE}")
    index.record("i1", f"/lot-tracker/{SCHEDULES_FILE}")

    index.forget("i1", f"/lot-tracker/{SCHEDULES_FILE}")

    assert index.paths("i1") == [f"/scrap-review/{SCHEDULES_FILE}"]


def test_dropping_the_last_path_removes_the_item_from_the_sweep(index: ScheduleIndex):
    """An item whose last schedule is gone must stop being read. Left behind, the
    sweep pays for it on every pass forever."""
    index.record("i1", f"/scrap-review/{SCHEDULES_FILE}")

    index.forget("i1", f"/scrap-review/{SCHEDULES_FILE}")

    assert index.items() == []


def test_forgetting_something_never_recorded_is_quiet(index: ScheduleIndex):
    """The sweep drops paths it could not read. It must not matter whether the
    row was already gone — two pods can sweep the same item at once."""
    index.forget("i1", f"/scrap-review/{SCHEDULES_FILE}")  # must not raise

    assert index.items() == []


# ── the hook, at the chokepoint every write shares ───────────────────────────


def test_the_index_is_fed_by_the_write_path_itself():
    """Not by a route. A page writes `schedules.json` through the file PUT
    route; the agent writes the SAME file through its `write_file` tool, which
    never touches a route; a workflow uses neither. Hooking routes would catch
    one and miss the others, and the miss would be silent — the schedule simply
    never fires and nothing says why.

    So the hook sits where the quota gate sits: the one call every write makes.
    """
    import asyncio

    from workspace_app.files import WorkspaceFiles
    from workspace_app.filestore.memory import MemoryFileStore

    seen: list[tuple[str, str]] = []
    files = WorkspaceFiles(MemoryFileStore(), on_write=lambda wid, path: seen.append((wid, path)))

    asyncio.run(files.write("i1", "/scrap-review/schedules.json", b"{}"))
    asyncio.run(files.write("i1", "/scrap-review/data.json", b"{}"))

    assert seen == [
        ("i1", "/scrap-review/schedules.json"),
        ("i1", "/scrap-review/data.json"),
    ]


def test_a_failing_hook_never_fails_the_write():
    """The bytes are committed before the hook runs. Raising here would report a
    failure for something that SUCCEEDED, and the caller would reasonably retry
    a write that already landed."""
    import asyncio

    from workspace_app.files import WorkspaceFiles
    from workspace_app.filestore.memory import MemoryFileStore

    def _boom(_wid: str, _path: str) -> None:
        raise RuntimeError("the index is down")

    files = WorkspaceFiles(MemoryFileStore(), on_write=_boom)

    asyncio.run(files.write("i1", "/scrap-review/schedules.json", b"{}"))  # must not raise

    assert asyncio.run(files.read("i1", "/scrap-review/schedules.json")) == b"{}"
