"""The listing route claims to read a schedules file THE WAY THE SWEEP DOES.

That claim is only worth anything as a comparison: the same file handed to
both, and the rows the route says will fire are exactly the rows the sweep
fires. The sweep is the oracle — it is the thing that actually runs work — and
every input below is one a real file can hold. A row the route shows as
runnable that the sweep skips is the panel promising a report that never
arrives; the reverse is a schedule nobody can see.

Each row targets its OWN workflow so a fired run names the row that fired it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

WORKFLOWS = ["w0", "w1", "w2"]


def _workflow(title: str) -> str:
    return json.dumps(
        {
            "id": "ignored",
            "title": title,
            "phases": [{"id": "p"}],
            # A sandbox step: no model needed, and MockSandbox answers `echo`.
            "steps": [{"type": "sandbox", "run": "echo ok", "phase": "p", "cache": False}],
        }
    )


def _app(*, max_rows: int = 100) -> tuple[TestClient, FastAPI, MemoryFileStore, str]:
    spec = make_spec()
    filestore = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()]),
        trigger_check_interval=timedelta(hours=1),
        max_page_schedules=max_rows,
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    return TestClient(app), app, filestore, item_id


def _base(item_id: str) -> str:
    return f"/api/a/playground/items/{item_id}"


def _rows(*rows: object) -> bytes:
    return json.dumps({"schedules": list(rows)}).encode()


# Every case: the file's bytes, and which row indices the SWEEP must fire.
CASES: list[tuple[str, bytes, set[int], int]] = [
    (
        "all good",
        _rows({"every": "hourly", "run": "w0"}, {"every": "minutes", "n": 1, "run": "w1"}),
        {0, 1},
        100,
    ),
    (
        "one names a workflow the item lacks",
        _rows({"every": "hourly", "run": "w0"}, {"every": "hourly", "run": "gone"}),
        {0},
        100,
    ),
    (
        "one row is refused by the linter",
        _rows({"every": "hourly", "run": "w0"}, {"every": "day", "run": "w1"}),
        {0},
        100,
    ),
    ("one row is not an object", _rows(5, {"every": "hourly", "run": "w1"}), {1}, 100),
    # Over the deployment's cap the sweep refuses the WHOLE file.
    (
        "over the cap",
        _rows(
            {"every": "hourly", "run": "w0"},
            {"every": "hourly", "run": "w1"},
            {"every": "hourly", "run": "w2"},
        ),
        set(),
        2,
    ),
    # `null` is how a page generator writes a field it left unset: daily.
    ("every is null", _rows({"every": None, "run": "w0"}), {0}, 100),
    # A byte that is not UTF-8 inside a payload string. The sweep decodes with
    # `replace` and runs the row; the route used to raise on it.
    (
        "not utf-8",
        b'{"schedules": [{"every": "hourly", "run": "w0", "with": {"note": "\xff"}}]}',
        {0},
        100,
    ),
    ("not json", b"{not json", set(), 100),
]


@pytest.mark.parametrize(("name", "content", "fired", "max_rows"), CASES, ids=[c[0] for c in CASES])
def test_the_route_marks_runnable_exactly_the_rows_the_sweep_fires(
    name: str, content: bytes, fired: set[int], max_rows: int
) -> None:
    client, app, _, item_id = _app(max_rows=max_rows)
    with client:
        for w in WORKFLOWS:
            r = client.put(f"{_base(item_id)}/files/.workflows/{w}.json", content=_workflow(w))
            assert r.status_code == 204
        r = client.put(f"{_base(item_id)}/files/.workflows/schedules.json", content=content)
        assert r.status_code == 204

        listed = client.get(f"{_base(item_id)}/schedules")
        assert listed.status_code == 200, listed.text
        # Wake the sandbox BEFORE the tick. The first row's run wakes it
        # otherwise, and the second row's manifest read — routed warm-first by
        # the facade, which does not check `.ready` — can land on a sandbox
        # still restoring (a pre-existing race, recorded as unfixed) and fail to
        # start. That is the oracle misfiring, not the route, and it made this
        # case red one run in five.
        woke = client.post(f"{_base(item_id)}/exec", json={"cmd": ["echo", "hi"]})
        assert woke.status_code == 200, woke.text
        assert client.portal is not None
        client.portal.call(app.state.user_schedule_sweeper.tick)
        runs = client.get(f"{_base(item_id)}/runs").json()

    body = listed.json()
    route_runnable = {row["index"] for row in body["rows"] if row["runnable"]}
    sweep_fired = {WORKFLOWS.index(run["workflow_id"]) for run in runs}
    assert sweep_fired == fired, f"{name}: the oracle itself did not do what the case expects"
    assert route_runnable == sweep_fired, name
    # A runnable row is one the panel may give a "next" for; a row the sweep
    # will not fire must not carry one.
    for row in body["rows"]:
        assert bool(row["next_run"]) == row["runnable"], (name, row)


def test_a_file_the_sweep_has_not_been_told_about_is_not_runnable() -> None:
    """The sweep reads only items in its index. A file that reached the store
    past every hook (a raw-store writer, a shell step before any chat turn) is
    invisible to it until the next turn's reconcile — and the route must not
    show its rows as if they will fire. Same shape as the other cases: the sweep
    is the oracle, and here it fires nothing."""
    client, app, filestore, item_id = _app()
    with client:
        r = client.put(f"{_base(item_id)}/files/.workflows/w0.json", content=_workflow("w0"))
        assert r.status_code == 204
        asyncio.run(
            filestore.write(
                item_id, "/.workflows/schedules.json", _rows({"every": "hourly", "run": "w0"})
            )
        )

        body = client.get(f"{_base(item_id)}/schedules").json()
        assert client.portal is not None
        client.portal.call(app.state.user_schedule_sweeper.tick)
        runs = client.get(f"{_base(item_id)}/runs").json()

    assert runs == []
    assert body["indexed"] is False
    assert [row["runnable"] for row in body["rows"]] == [False]
