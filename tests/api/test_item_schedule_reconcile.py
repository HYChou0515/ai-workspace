"""An item's own schedules file is found however it was written.

`save_schedules` writes through the facade, which tells the index. A file can
also reach the workspace past that hook, and two mechanisms exist to catch it:
the mirror's `on_write` (WUI P40 — fires when the sandbox is mirrored to the
store, which a host-managed deployment never does through the mirror) and the
turn-end reconcile (WUI P48 — lists the workspace after every chat turn). Both
predate the item-level location. Two doors, two tests, and each says what it
pins — because the first version of this file asserted AFTER the `TestClient`
exit, where the shutdown writeback satisfied it, and could not redden on any
change to the reconcile at all.

Both assert INSIDE the client, at the end of the turn: a poll that times out
fails.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.api.schedule_index import ScheduleIndex
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

SCHEDULES = "/.workflows/schedules.json"
ROWS = json.dumps({"schedules": [{"every": "hourly", "run": "nightly"}]}).encode()


def _app() -> tuple[FastAPI, ScheduleIndex, MemoryFileStore, MockSandbox, str]:
    spec = make_spec(default_user="u")
    filestore = MemoryFileStore()
    sandbox = MockSandbox()
    app = create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=filestore,
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        trigger_check_interval=timedelta(hours=1),
        # Parked: the periodic mirror must not be what indexes the file mid-test.
        mirror_interval=timedelta(hours=1),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    return app, ScheduleIndex(spec), filestore, sandbox, item_id


def _turn_then_wait_for_index(client: TestClient, base: str, index: ScheduleIndex, item_id: str):
    r = client.post(f"{base}/messages", json={"content": "hi"})
    assert r.status_code in (200, 202), r.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not index.paths(item_id):
        time.sleep(0.05)
    # Inside the client on purpose: after it exits, the shutdown writeback
    # would index the file whatever the turn-end hook did.
    assert index.paths(item_id) == [SCHEDULES], "not indexed by the end of the turn"


def test_a_file_written_straight_into_the_store_is_indexed_by_the_turn_end_reconcile() -> None:
    """The reconcile's own door. No sandbox exists, so no mirror and no hook —
    ONLY the turn-end reconcile can index this (a `seed_item`, a raw-store
    writer). Verified: with `reconcile_item_schedules` made a no-op this test
    fails; it is the one that pins the reconcile."""
    app, index, filestore, _, item_id = _app()
    base = f"/api/a/playground/items/{item_id}"
    asyncio.run(filestore.write(item_id, SCHEDULES, ROWS))

    with TestClient(app) as client:
        assert index.paths(item_id) == [], "nothing has told the index yet"
        _turn_then_wait_for_index(client, base, index, item_id)


def test_a_file_written_inside_the_sandbox_is_indexed_by_the_end_of_a_turn() -> None:
    """The shell door: the bytes exist only inside a woken sandbox, and the
    durable store has not seen them when the turn ends. On this deployment
    shape (`kind: mock`/`local`) the turn-end flush mirrors the sandbox to the
    store and the mirror's `on_write` hook indexes the file BEFORE the reconcile
    runs — so this pins the door, not which of the two mechanisms answered.
    (Measured: with the reconcile a no-op it still passes; the host-managed
    branch, where only the reconcile can, is out of reach with MockSandbox.)"""
    app, index, filestore, sandbox, item_id = _app()
    base = f"/api/a/playground/items/{item_id}"

    with TestClient(app) as client:
        # A shell command wakes the sandbox — the same door the agent's `exec`
        # tool and the terminal use.
        r = client.post(f"{base}/exec", json={"cmd": ["echo", "hi"]})
        assert r.status_code == 200, r.text
        handle = sandbox.handle_for_id(item_id)
        assert handle is not None
        asyncio.run(sandbox.upload(handle, ROWS, SCHEDULES))
        assert asyncio.run(filestore.ls(item_id, "/.workflows/")) == [], (
            "the file must not be in the durable store yet — that is the point"
        )
        assert index.paths(item_id) == [], "nothing has told the index yet"
        _turn_then_wait_for_index(client, base, index, item_id)
