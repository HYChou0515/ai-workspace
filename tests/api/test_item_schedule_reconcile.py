"""An item's own schedules file is found however it was written.

`save_schedules` writes through the facade, which tells the index. But a file
can reach the workspace past every hook — an agent's `exec` running a shell
command, a workflow's shell step, a writer that hits the raw store — and the
turn-end reconcile (P48 of the WUI plan) is what closes that: it lists the
workspace and records every declaration it finds. That reconcile predates the
item-level location; this pins that `.workflows/schedules.json` is one of the
paths it counts, driven through the real entrance (a chat turn), not the
function.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

from fastapi.testclient import TestClient

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.api.schedule_index import ScheduleIndex
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def test_a_schedules_file_written_past_every_hook_is_indexed_after_a_turn() -> None:
    spec = make_spec(default_user="u")
    filestore = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        trigger_check_interval=timedelta(hours=1),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    # Straight into the store, past the facade AND the mirror — the shape of a
    # shell-written file on a host-managed deployment, or of `seed_item`.
    rows = json.dumps({"schedules": [{"every": "hourly", "run": "nightly"}]}).encode()
    asyncio.run(filestore.write(item_id, "/.workflows/schedules.json", rows))
    index = ScheduleIndex(spec)

    with TestClient(app) as client:
        assert index.paths(item_id) == [], "nothing has told the index yet"
        r = client.post(f"/api/a/playground/items/{item_id}/messages", json={"content": "hi"})
        assert r.status_code in (200, 202), r.text
        # The reconcile runs at turn END, after the reply; give the turn a moment
        # to finish rather than asserting on the POST's return.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not index.paths(item_id):
            time.sleep(0.05)

    assert index.paths(item_id) == ["/.workflows/schedules.json"]
