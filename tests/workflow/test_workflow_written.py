"""`ScheduleAuthorship.workflow_written` (`docs/plan-seam-token-refresh.md` W1–W3):
a workflow rewritten by a person hands the bound schedules running it to them.

The stores are the real ones over an in-memory spec; the schedules file and the
workflows are written straight to the file store, past every door.
"""

from __future__ import annotations

import json

from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.workflow.schedule_bindings import (
    ScheduleAuthorship,
    ScheduleBinding,
    ScheduleBindings,
    file_keys,
    register_schedule_bindings,
    workflow_digest,
)

ITEM = "inv"
SCHED = "/.workflows/schedules.json"
BROKEN = b'{"id":"x","phases":[{"id":"p"}],"steps":[{"type":"agent","prompt":"hi","phase":"p"}]}'


def _wf(title: str) -> bytes:
    return json.dumps(
        {
            "id": "ignored",
            "title": title,
            "phases": [{"id": "p"}],
            "steps": [
                {"type": "agent", "cache": True, "prompt": "hi", "phase": "p", "out": "o.md"}
            ],
        }
    ).encode()


class _World:
    def __init__(self, *, may_run: bool = True) -> None:
        spec = make_spec(default_user="x")
        register_schedule_bindings(spec)
        self.files = WorkspaceFiles(MemoryFileStore())
        self.bindings = ScheduleBindings(spec)
        self.moved: list[tuple[str, str, str]] = []
        self.authorship = ScheduleAuthorship(
            self.bindings,
            read=self.files.read,
            may_run=lambda _user, _item: may_run,
            on_moved=lambda prev, who, wf: self.moved.append((prev.user_id, who, wf)),
        )

    async def write(self, workflow_id: str, body: bytes) -> bytes:
        await self.files.write(ITEM, f"/.workflows/{workflow_id}.json", body)
        return body

    async def schedule(self, *runs: str) -> list[str]:
        """One daily row per workflow named; their keys, in that order."""
        raw = json.dumps({"schedules": [{"every": "daily", "at": "09:00", "run": r} for r in runs]})
        await self.files.write(ITEM, SCHED, raw.encode())
        return [key for key, _row in file_keys(ITEM, SCHED, raw)]

    async def bind(self, key: str, user: str, workflow_id: str, *, reason: str = "self") -> None:
        digest = await workflow_digest(self.files.read, ITEM, workflow_id)
        self.bindings.bind(
            key, item_id=ITEM, path=SCHED, user_id=user, workflow_digest=digest, reason=reason
        )

    def binding(self, key: str) -> ScheduleBinding:
        found = self.bindings.get(key)
        assert found is not None
        return found


async def test_someone_elses_rewrite_hands_them_the_schedule_and_says_why():
    """W1/W3: the reason is the workflow they changed — not "wrote the
    schedule", which they did not."""
    w = _World()
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")

    written = await w.write("nightly", _wf("v2"))
    moved = await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written)

    assert moved == [key]
    got = w.binding(key)
    assert (got.user_id, got.reason) == ("bob", "workflow")
    assert got.workflow_digest == await workflow_digest(w.files.read, ITEM, "nightly")
    assert w.moved == [("carol", "bob", "nightly")]


async def test_the_binders_own_rewrite_renews_their_consent_and_keeps_their_reason():
    w = _World()
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly", reason="author")

    written = await w.write("nightly", _wf("v2"))
    await w.authorship.workflow_written(ITEM, "nightly", author="carol", written=written)

    got = w.binding(key)
    assert (got.user_id, got.reason) == ("carol", "author")
    assert got.workflow_digest == await workflow_digest(w.files.read, ITEM, "nightly")
    assert w.moved == []


async def test_only_the_schedules_running_the_rewritten_workflow_move():
    """Review round 1 (conformance F1a): carol's row runs ANOTHER workflow."""
    w = _World()
    await w.write("nightly", _wf("v1"))
    await w.write("weekly", _wf("w1"))
    nightly, weekly = await w.schedule("nightly", "weekly")
    await w.bind(nightly, "carol", "nightly")
    await w.bind(weekly, "carol", "weekly")

    written = await w.write("nightly", _wf("v2"))
    await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written)

    assert w.binding(nightly).user_id == "bob"
    assert w.binding(weekly).user_id == "carol"


async def test_a_rewrite_that_will_not_parse_moves_nothing():
    """Review round 1 (conformance F1b): it would record "", the PACKAGE
    workflow's consent, which a later delete satisfies (viewer-login round 6)."""
    w = _World()
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")
    before = w.binding(key).workflow_digest

    written = await w.write("nightly", BROKEN)
    assert await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written) == []

    assert (w.binding(key).user_id, w.binding(key).workflow_digest) == ("carol", before)


async def test_writing_the_same_workflow_again_takes_nothing():
    """Review round 1 (conformance F1c): nothing changed, so nothing to consent
    to — carol keeps it and hears nothing."""
    w = _World()
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")

    written = await w.write("nightly", _wf("v1"))
    await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written)

    assert w.binding(key).user_id == "carol"
    assert w.moved == []


async def test_a_writer_whose_bytes_were_overwritten_since_takes_nothing():
    """Review round 1 (defect F2): alice saves, mallory saves, then alice's
    hook runs. The workflow now holds mallory's body — alice never consented
    to it, so she takes nothing; mallory's own hook decides."""
    w = _World()
    await w.write("nightly", _wf("orig"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "bob", "nightly")

    alices = await w.write("nightly", _wf("alice"))
    mallorys = await w.write("nightly", _wf("mallory"))
    await w.authorship.workflow_written(ITEM, "nightly", author="alice", written=alices)

    assert w.binding(key).user_id == "bob"
    await w.authorship.workflow_written(ITEM, "nightly", author="mallory", written=mallorys)
    assert w.binding(key).user_id == "mallory"
    assert w.moved == [("bob", "mallory", "nightly")]


async def test_someone_who_may_not_run_work_here_takes_nothing():
    w = _World(may_run=False)
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")

    written = await w.write("nightly", _wf("v2"))
    await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written)

    assert w.binding(key).user_id == "carol"


async def test_nobody_takes_nothing():
    w = _World()
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")

    written = await w.write("nightly", _wf("v2"))
    await w.authorship.workflow_written(ITEM, "nightly", author="", written=written)

    assert w.binding(key).user_id == "carol"


async def test_a_failed_notice_does_not_undo_the_move():
    """Review round 1 (conformance F9): the hook runs after the bytes landed."""
    w = _World()

    def boom(_prev: ScheduleBinding, _who: str, _wf: str) -> None:
        raise RuntimeError("mail down")

    w.authorship = ScheduleAuthorship(
        w.bindings, read=w.files.read, may_run=lambda _u, _i: True, on_moved=boom
    )
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")

    written = await w.write("nightly", _wf("v2"))
    await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written)

    assert w.binding(key).user_id == "bob"


async def test_a_schedules_file_that_cannot_be_read_moves_nothing_and_raises_nothing():
    """Review round 1 (conformance F9): which rows run the workflow is unknown."""
    w = _World()
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")
    written = await w.write("nightly", _wf("v2"))
    real = w.files.read

    async def read(item: str, path: str) -> bytes:
        if path == SCHED:
            raise OSError("store down")
        return await real(item, path)

    w.authorship = ScheduleAuthorship(w.bindings, read=read, may_run=lambda _u, _i: True)

    assert await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written) == []
    assert w.binding(key).user_id == "carol"


async def test_a_store_that_fails_outright_raises_nothing():
    """`workflow_written` never raises: the file it follows has landed."""
    w = _World()
    await w.write("nightly", _wf("v1"))
    (key,) = await w.schedule("nightly")
    await w.bind(key, "carol", "nightly")

    def broken_list(_item: str) -> list[ScheduleBinding]:
        raise OSError("db down")

    w.bindings.for_item = broken_list  # ty: ignore[invalid-assignment]
    written = await w.write("nightly", _wf("v2"))

    assert await w.authorship.workflow_written(ITEM, "nightly", author="bob", written=written) == []
