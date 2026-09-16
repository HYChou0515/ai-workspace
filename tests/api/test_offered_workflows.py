"""One list of "which workflows may this item start", for every entrance.

Three entrances start a workflow on an item — the Workflows panel's Run, a
page's `startRun`, and a `schedules.json` row — and two of them used to consult
a different list. The panel resolved a profile workflow OR one the item authored
in `.workflows/<id>.json`; the page and the schedule consulted the profile alone.
On an interactive profile that list is EMPTY, so a workflow the agent had just
saved was startable from the panel and refused everywhere else, with an error
that called it an authorisation problem ("This app does not offer …"). There was
nothing to authorise: the gate never looked in the folder.

These tests drive the REAL composition root — `create_app` — because the rule
lives in how the entrances are wired, and each entrance's own tests inject the
list as a double.
"""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

# A well-formed workflow.json with one agent step, the smallest thing the
# interpreter will run. The scripted runner answers it in one turn.
_NIGHTLY = json.dumps(
    {
        "id": "ignored",
        "title": "Nightly",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "cache": True, "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
)


# A workflow file that will not parse: an agent step with no `cache` (required).
_BROKEN = b'{"id":"x","phases":[{"id":"p"}],"steps":[{"type":"agent","prompt":"hi","phase":"p"}]}'


def _app() -> tuple[TestClient, FastAPI, str]:
    """A playground item on its `default` profile — which declares NO workflow,
    the shape every ordinary chat item has."""
    spec = make_spec()
    runner = ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=runner,
        # Schedules are opt-in per deploy (0 = off, in silence). An hour, so the
        # background loop never wakes during a test and the tick below is the
        # only sweep that runs.
        trigger_check_interval=timedelta(hours=1),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    return TestClient(app), app, item_id


def _base(item_id: str) -> str:
    return f"/api/a/playground/items/{item_id}"


def test_the_panel_lists_a_workflow_that_wont_parse_with_its_problem() -> None:
    client, _app_, item_id = _app()
    with client:
        r = client.put(f"{_base(item_id)}/files/.workflows/broken.json", content=_BROKEN)
        assert r.status_code == 204
        listed = client.get(f"{_base(item_id)}/workflows")
    assert listed.status_code == 200, listed.text
    rows = {w["id"]: w for w in listed.json()}
    assert "broken" in rows, "a file that will not parse vanished from the listing"
    assert "`cache` is required" in rows["broken"]["problem"]
    assert rows["broken"]["phases"] == []


def test_a_page_can_start_a_workflow_the_item_authored() -> None:
    """The reported case: `save_workflow` had written `.workflows/<id>.json`, the
    panel could Run it, and the page's button answered 403."""
    client, _, item_id = _app()
    with client:
        put = client.put(f"{_base(item_id)}/files/.workflows/nightly.json", content=_NIGHTLY)
        assert put.status_code == 204

        resp = client.post(f"{_base(item_id)}/wui/run", json={"workflow": "nightly"})

    assert resp.status_code == 200, resp.text


def test_a_page_asking_for_a_workflow_the_item_lacks_is_told_what_it_has() -> None:
    """The sentence a person reads in the page's error panel. It used to say
    "This app does not offer X to its pages" — an authorisation nobody could
    grant. The true cause is that this ITEM has no such workflow, and what it
    does have is the only thing the reader can act on. No tool name: the reader
    is pressing a button, not driving the agent."""
    client, _, item_id = _app()
    with client:
        put = client.put(f"{_base(item_id)}/files/.workflows/nightly.json", content=_NIGHTLY)
        assert put.status_code == 204

        resp = client.post(f"{_base(item_id)}/wui/run", json={"workflow": "nigthly"})

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "This item has no workflow named 'nigthly'" in detail
    assert "(it has: nightly)" in detail
    assert "save_workflow" not in detail


def test_a_schedule_can_name_a_workflow_the_item_authored() -> None:
    """The other refused entrance. A `schedules.json` row whose `run` was a
    workspace workflow was skipped every tick with a log line saying the app
    offers nothing — the same list the page consulted, consulted by the sweep.

    Driven through the app's own sweeper (one tick, in the app's loop), because
    the sweep's own tests inject the list as a double: what is under test here
    is which list `create_app` hands it.
    """
    client, app, item_id = _app()
    with client:
        put = client.put(f"{_base(item_id)}/files/.workflows/nightly.json", content=_NIGHTLY)
        assert put.status_code == 204
        # Every minute: due on the very first tick, whatever the clock says.
        rows = json.dumps({"schedules": [{"every": "minutes", "n": 1, "run": "nightly"}]})
        put = client.put(f"{_base(item_id)}/files/.workflows/schedules.json", content=rows)
        assert put.status_code == 204

        assert client.portal is not None  # inside `with client:` — the app's own loop
        fired = client.portal.call(app.state.user_schedule_sweeper.tick)

        runs = client.get(f"{_base(item_id)}/runs").json()

    assert fired == 1
    assert [r["workflow_id"] for r in runs] == ["nightly"]
