"""Which items have schedules — so the sweep does not read every item, ever.

Today's trigger sweep enumerates apps × profiles: a small, static set. Reading
item-level declarations changes that to "every item", and items grow without
limit. This index is the answer, and it is the same shape the platform already
uses for per-item facts it must look up without a session (`_SandboxActivity`,
`_SandboxAddress`): one opaque row per item.

It is maintained on the WRITE path, not in a route. A page writes its
`schedules.json` through the file PUT route; the agent writes the same file
through its `write_file` tool, which never touches a route. Hooking routes would
catch one and miss the other.

There is no ONE call every write makes — believing there was is what shipped this
index blind, because the PUT route's streaming upload does not go through
`_write_unchecked`. So the coverage test below derives the write paths from the
facade's own source instead of naming them, and one test enters where a page
does: the PUT route itself.

The index is allowed to be stale in ONE direction: it may name a file that has
since been deleted. That is why the sweep re-reads and drops what it cannot
find, and it is why deletes need no hook of their own.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar
from specstar.types import PreconditionFailedError, RevisionStatus

from workspace_app.api.schedule_index import (
    SCHEDULES_FILE,
    ScheduleIndex,
    _ScheduleIndex,
    is_schedule_file,
    register_schedule_index,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec

from .conftest import Harness


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


# ── the hook, on every path that lands bytes ─────────────────────────────────


def test_the_index_is_fed_by_the_write_path_itself():
    """Not by a route. A page writes `schedules.json` through the file PUT
    route; the agent writes the SAME file through its `write_file` tool, which
    never touches a route; a workflow uses neither. Hooking routes would catch
    one and miss the others, and the miss would be silent — the schedule simply
    never fires and nothing says why.

    There is no single call every write makes — that belief is what shipped
    this index blind. The coverage test further down derives the write paths
    from the facade's source; this one pins the ordinary case.
    """
    import asyncio

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

    from workspace_app.filestore.memory import MemoryFileStore

    def _boom(_wid: str, _path: str) -> None:
        raise RuntimeError("the index is down")

    files = WorkspaceFiles(MemoryFileStore(), on_write=_boom)

    asyncio.run(files.write("i1", "/scrap-review/schedules.json", b"{}"))  # must not raise

    assert asyncio.run(files.read("i1", "/scrap-review/schedules.json")) == b"{}"


def test_a_concurrent_record_does_not_lose_the_other_pods_path(index: ScheduleIndex) -> None:
    """Read-modify-write on a row two pods share. Both read `["p1"]`, one writes
    `["p1","p2"]` and the other `["p1","p3"]` — and one page's schedules stop
    existing, permanently, because only a WRITE of that file puts it back.

    Simulated rather than raced: the in-memory backend's etag is not atomic, so
    a real race here proves nothing either way. What is asserted is that a
    REFUSED write is re-read and retried instead of being the last word.
    """
    index.record("i1", "/a/schedules.json")

    rm = index._spec.get_resource_manager(_ScheduleIndex)
    real_modify = rm.modify
    refused: list[int] = []

    def _lose_once(*args, **kw):
        if not refused:
            refused.append(1)
            # Another pod committed between our read and our write, and added
            # its own path while doing so.
            real_modify(
                "i1",
                _ScheduleIndex(paths=["/a/schedules.json", "/b/schedules.json"]),
                status=RevisionStatus.draft,
            )
            raise PreconditionFailedError("i1", "ours", "theirs")
        return real_modify(*args, **kw)

    rm.modify = _lose_once  # ty: ignore[invalid-assignment]
    try:
        index.record("i1", "/c/schedules.json")
    finally:
        rm.modify = real_modify  # ty: ignore[invalid-assignment]

    assert refused, "the test never made anyone lose — it is measuring nothing"
    assert index.paths("i1") == [
        "/a/schedules.json",
        "/b/schedules.json",
        "/c/schedules.json",
    ]


def test_the_merge_is_conditional_on_what_it_read(index: ScheduleIndex) -> None:
    """The retry loop is only half the mechanism. Re-reading after a conflict is
    worth nothing if the write never ASKS to be conditional — and a
    read-modify-write with no etag simply overwrites the peer, so there is no
    conflict to retry and nothing to notice.

    Asserted with a store that ENFORCES the precondition rather than one that
    records it: dropping `expected_etag` leaves every other test in this file
    green, because the in-memory backend accepts an unconditional write happily.
    The only thing that can tell the difference is a writer that refuses one.
    """
    index.record("i1", "/a/schedules.json")
    rm = index._spec.get_resource_manager(_ScheduleIndex)
    real_modify = rm.modify
    asked: list[str | None] = []

    def _enforcing(resource_id, data, /, **kw):
        etag = kw.get("expected_etag")
        asked.append(etag)
        current = rm.get(resource_id).info.etag
        if etag != current:
            # What a real backend does to an unconditional or stale write when
            # another writer holds the row.
            raise PreconditionFailedError(resource_id, etag or "<none>", current)
        return real_modify(resource_id, data, **kw)

    rm.modify = _enforcing  # ty: ignore[invalid-assignment]
    try:
        wrote = index.record("i1", "/b/schedules.json")
    finally:
        rm.modify = real_modify  # ty: ignore[invalid-assignment]

    assert asked, "nothing was written — this guard is measuring nothing"
    assert wrote is True
    assert index.paths("i1") == ["/a/schedules.json", "/b/schedules.json"]


def test_a_backend_that_is_down_is_not_mistaken_for_a_lost_race(index: ScheduleIndex) -> None:
    """`record` swallowed every exception around its create, so "another pod got
    there first" and "the store is broken" produced the same silence — and the
    index simply had no row, which the sweep reads as "this item has no
    schedules". That is the failure this whole module exists to make impossible,
    arriving through its own error handling.
    """
    rm = index._spec.get_resource_manager(_ScheduleIndex)
    real_create = rm.create

    def _down(*args, **kw):
        raise RuntimeError("the database is unreachable")

    rm.create = _down  # ty: ignore[invalid-assignment]
    try:
        with pytest.raises(RuntimeError):
            index.record("i1", "/a/schedules.json")
    finally:
        rm.create = real_create  # ty: ignore[invalid-assignment]


def test_a_soft_deleted_row_comes_back_rather_than_stalling(index: ScheduleIndex) -> None:
    """specstar deletes SOFTLY, and `exists` is deletion-blind — so a deleted row
    answers "duplicate" to a create and "absent" to a read. Anything that does
    not resolve that disagreement stalls: the create refuses, the read says
    nothing is there, and asking again changes neither.

    The first attempt at this recursed instead, which would have spent the stack
    on a thousand store round trips and been swallowed whole by `_landed`'s
    `except Exception`. The second attempt caught the error where `_res` had
    already hidden it, so the branch could never run at all — `pragma: no cover`
    was the tell, and it is gone now because this exercises it.
    """
    index.record("i1", "/a/schedules.json")
    index._spec.get_resource_manager(_ScheduleIndex).delete("i1")

    wrote = index.record("i1", "/b/schedules.json")

    assert wrote is True
    assert index.paths("i1") == ["/a/schedules.json", "/b/schedules.json"]
    assert index.items() == ["i1"]


def test_a_page_in_a_nested_folder_counts_too() -> None:
    """A page is a folder, and nothing says that folder must sit at the top.
    `wuiFolder` accepts any depth and `writeFile`'s boundary is the page's OWN
    folder, so a page at `/reports/scrap/` writes `/reports/scrap/schedules.json`
    quite legitimately — and a depth-2 rule dropped it on the floor with no
    error anywhere. The page saves, sees its file, and nothing ever runs."""
    assert is_schedule_file(f"/reports/scrap/{SCHEDULES_FILE}")
    assert is_schedule_file(f"/a/b/c/{SCHEDULES_FILE}")


def test_a_derivative_folder_cannot_schedule_work() -> None:
    """Any depth is allowed, so anything NAMED `schedules.json` anywhere could
    start runs — including one vendored into `node_modules/` or unpacked from
    somebody's archive. That is work nobody declared, fired from a folder they
    have never opened.

    The list is the mirror's: a file the platform declines to BACK UP is not one
    it should take instructions from, and sharing it means the two cannot
    disagree.
    """
    for path in (
        f"/page/node_modules/some-lib/{SCHEDULES_FILE}",
        f"/page/.venv/lib/{SCHEDULES_FILE}",
        f"/page/__pycache__/{SCHEDULES_FILE}",
    ):
        assert not is_schedule_file(path), path


def test_a_real_page_folder_still_counts() -> None:
    """The control. A rule that refused anything with a dot or a nested path
    would pass the test above and switch the feature off."""
    for path in (
        f"/reports/{SCHEDULES_FILE}",
        f"/reports/scrap/{SCHEDULES_FILE}",
        f"/my.page/{SCHEDULES_FILE}",
    ):
        assert is_schedule_file(path), path


def test_the_workspace_root_still_does_not_count() -> None:
    """The control for the loosening above: a schedule belongs to a page, a page
    is a folder, and a view file at the root has no folder of its own — it
    cannot write, so it cannot be a page."""
    assert not is_schedule_file(f"/{SCHEDULES_FILE}")
    assert not is_schedule_file(SCHEDULES_FILE)


# ── every path that lands bytes, not just the one we happened to hook ────────


def _methods_that_land_bytes() -> set[str]:
    """Derived from the facade's source, never typed out here.

    A hand-written list of write paths IS the bug this guards against. The hook
    went into `_write_unchecked` in the belief that it was "the chokepoint every
    write shares"; `write_from_path` — the streaming upload the file PUT route
    uses, which is the path a WUI page's own `writeFile` takes — keeps its own
    copy of that tail and never called it. So the index never saw the file, and
    the schedules never fired, in silence.

    A method lands bytes if it names a byte-writing primitive itself, or defers
    to the one private tail that does. Add another write path and this set grows
    on its own, and the test below fails until that path is covered.

    It returns SEVEN today (`create`, `create_exclusive`, `edit`, `move`,
    `write`, `write_from_path`, `write_record`). Only two of those were ever
    blind; the value of driving the rest is that it PROVES they were not,
    which reading could not.
    """
    import ast
    import inspect

    from workspace_app.files import facade

    tree = ast.parse(inspect.getsource(facade))
    cls = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "WorkspaceFiles"
    )
    primitives = {"upload", "upload_file", "write", "write_from_path", "_write_unchecked"}
    out: set[str] = set()
    for node in cls.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if isinstance(fn, ast.Attribute) and fn.attr in primitives:
                out.add(node.name)
            # `native(...)` — the filestore's own create-exclusive, reached by
            # `getattr`, so it is invisible as an attribute call.
            if isinstance(fn, ast.Name) and fn.id == "native":
                out.add(node.name)
    return {n for n in out if not n.startswith("_")}


class _CasStore(MemoryFileStore):
    """A store with optimistic concurrency, like the wiki store.

    `MemoryFileStore` has no `read_with_etag`/`write_cas`, so `edit` against it
    always takes the delegating branch — which is why driving `edit` proved
    nothing about the branch that writes for itself.
    """

    async def read_with_etag(self, workspace_id: str, path: str):
        try:
            data = await self.read(workspace_id, path)
        except Exception:
            return None
        return data, str(len(data))

    async def write_cas(self, workspace_id: str, path: str, data: bytes, etag: str | None) -> bool:
        await self.write(workspace_id, path, data)
        return True


def test_every_public_write_path_feeds_the_index() -> None:
    """`schedule_index.py` states the invariant "it may never miss one that
    exists". That is a claim about EVERY way bytes reach a path, and the facade
    has several — a page's PUT, the agent's tool, a copy, a move, a record.

    Each one is driven here rather than trusting a shared tail, because the tail
    is precisely what was not shared.
    """
    import asyncio
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from workspace_app.filestore.memory import MemoryFileStore

    def _drive(name: str) -> list[str]:
        seen: list[str] = []
        files = WorkspaceFiles(MemoryFileStore(), on_write=lambda _w, p: seen.append(p))
        target = f"/scrap-review/{SCHEDULES_FILE}"
        if name == "write":
            asyncio.run(files.write("i1", target, b"[]"))
        elif name == "create_exclusive":
            asyncio.run(files.create_exclusive("i1", target, b"[]"))
        elif name == "write_record":
            asyncio.run(files.write_record("i1", target, b"[]"))
        elif name == "move":
            asyncio.run(files.write("i1", "/scrap-review/draft.json", b"[]"))
            seen.clear()
            asyncio.run(files.move("i1", "/scrap-review/draft.json", target))
        elif name == "write_from_path":
            with TemporaryDirectory() as d:
                src = Path(d) / "staged.json"
                src.write_bytes(b"[]")
                asyncio.run(files.write_from_path("i1", target, src))
        elif name == "create":
            asyncio.run(files.create("i1", target, b"[]"))
        elif name == "edit":
            # An edit REPLACES the declaration: "every weekday" becomes "every
            # day" and the platform must re-read it. A page saving through the
            # agent's edit tool is the same event as saving through PUT.
            asyncio.run(files.write("i1", target, b'[{"every": "weekly"}]'))
            seen.clear()
            asyncio.run(files.edit("i1", target, "weekly", "daily"))
        elif name == "edit_cas":
            # `edit` has TWO branches and they land bytes in different places. A
            # store exposing `read_with_etag`/`write_cas` (the wiki store, here)
            # takes the optimistic-concurrency path, which never goes through
            # `write` — so driving `edit` against a plain store exercised the
            # delegating half and reported the whole method covered. That is the
            # same "one of several tails" mistake this guard exists to catch,
            # one level further down.
            files = WorkspaceFiles(_CasStore(), on_write=lambda _w, p: seen.append(p))
            asyncio.run(files.write("i1", target, b'[{"every": "weekly"}]'))
            seen.clear()
            asyncio.run(files.edit("i1", target, "weekly", "daily"))
        else:
            return [f"UNCOVERED:{name}"]
        return seen

    landing = _methods_that_land_bytes()
    assert landing, "the derivation found nothing — it is measuring nothing"

    # `edit_cas` is not a method name — it is `edit`'s SECOND branch, which lands
    # bytes somewhere else entirely. The derivation works on methods, so a branch
    # like that has to be named here; that is a real limit of the derivation and
    # is why it is written down rather than left implicit.
    covered = {name: _drive(name) for name in [*sorted(landing), "edit_cas"]}
    missed = [n for n, seen in covered.items() if SCHEDULES_FILE not in str(seen)]

    assert not missed, f"these land bytes without telling the index: {missed}"


def test_a_page_saving_its_schedules_through_the_file_route_is_indexed(
    harness: Harness,
) -> None:
    """Through the entrance a PAGE actually uses, end to end.

    The WUI bridge's `writeFile` is a PUT to the file route, which streams the
    body to a staging file and calls `write_from_path`. This module's own
    docstring named that route from the first commit — and every test in it went
    through `files.write` instead, which is why the miss survived.
    """
    # Inside the lifespan: the index model is registered there (post-`spec.apply`,
    # so specstar never emits bare CRUD routes for platform bookkeeping), and a
    # client that never starts it would measure a world that does not ship.
    with harness.client:
        r = harness.client.put(
            harness.wpath(f"/files/scrap-review/{SCHEDULES_FILE}"), content=b"[]"
        )

    assert r.status_code < 300, r.text
    assert ScheduleIndex(harness.spec).items() == [harness.iid]
