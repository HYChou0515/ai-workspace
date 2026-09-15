"""`GET /a/{slug}/items/{id}/schedules` — what the Workflows panel shows.

An item's schedules lived in a hidden folder and nowhere on screen: the agent
said "set up" and the person could only believe it. This reads the file back the
way the SWEEP reads it — same parser, same next-run rule, same ledger — so what
the panel shows is what will happen, including the row that will never fire
because its `run` is gone, the row the linter refuses, and the deployment whose
sweep is off. One implementation of the schedule math, in Python; the panel
only renders.
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

_NIGHTLY = json.dumps(
    {
        "id": "ignored",
        "title": "Nightly",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
)


def _app(*, sweep: bool = True) -> tuple[TestClient, FastAPI, str]:
    spec = make_spec()
    runner = ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=runner,
        trigger_check_interval=timedelta(hours=1) if sweep else None,
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    return TestClient(app), app, item_id


def _base(item_id: str) -> str:
    return f"/api/a/playground/items/{item_id}"


def _put(client: TestClient, item_id: str, path: str, body: str) -> None:
    assert client.put(f"{_base(item_id)}/files/{path}", content=body).status_code == 204


def test_an_item_with_no_schedules_file_answers_an_empty_list() -> None:
    client, _, item_id = _app()
    with client:
        r = client.get(f"{_base(item_id)}/schedules")
    assert r.status_code == 200
    assert r.json() == {
        "enabled": True,
        "indexed": False,
        "path": ".workflows/schedules.json",
        "rows": [],
        "problems": [],
    }


def test_the_rows_come_back_the_way_the_sweep_reads_them() -> None:
    client, _, item_id = _app()
    with client:
        _put(client, item_id, ".workflows/nightly.json", _NIGHTLY)
        _put(
            client,
            item_id,
            ".workflows/schedules.json",
            json.dumps(
                {
                    "schedules": [
                        {"every": "daily", "at": "09:00", "tz": "Asia/Taipei", "run": "nightly"},
                        {"every": "hourly", "run": "vanished"},  # workflow deleted since
                        {"every": "day", "run": "nightly"},  # the linter refuses `day`
                    ]
                }
            ),
        )
        r = client.get(f"{_base(item_id)}/schedules")

    body = r.json()
    assert body["enabled"] is True
    good, gone, bad = body["rows"]
    assert (good["run"], good["known"], good["describe"]) == (
        "nightly",
        True,
        "daily at 09:00 Asia/Taipei",
    )
    assert good["next_run"] and good["problems"] == []
    # The localisable pieces, derived from the same value as the sentence.
    assert good["tz"] == "Asia/Taipei"
    assert (good["next_at"] == "") == good["due_now"]
    # A `run` this item no longer has: shown, and flagged — the sweep will skip it
    # with a log line nobody reads, so the panel must be the one that says so.
    assert (gone["run"], gone["known"]) == ("vanished", False)
    # A row the linter refuses: shown WITH its problem, in file order, so the
    # panel can point at the line and a person can fix or remove it.
    assert bad["problems"] and bad["describe"] == ""
    # Every row carries what was written, so the panel can rewrite the file
    # minus one row without losing the others — the bad ones included.
    assert [row["raw"]["every"] for row in body["rows"]] == ["daily", "hourly", "day"]


def test_a_deployment_with_the_sweep_off_says_so() -> None:
    client, _, item_id = _app(sweep=False)
    with client:
        _put(client, item_id, ".workflows/nightly.json", _NIGHTLY)
        _put(
            client,
            item_id,
            ".workflows/schedules.json",
            json.dumps({"schedules": [{"every": "hourly", "run": "nightly"}]}),
        )
        r = client.get(f"{_base(item_id)}/schedules")

    body = r.json()
    assert body["enabled"] is False
    # The sweep never ticks on this deployment, so no row will fire — and a row
    # with a "next" under a "switched off" notice is the contradiction the
    # verdict exists to remove.
    (row,) = body["rows"]
    assert (row["run"], row["runnable"], row["next_run"], row["next_at"]) == (
        "nightly",
        False,
        "",
        "",
    )


def test_an_index_the_route_cannot_read_is_not_indexed_and_not_a_500(monkeypatch) -> None:
    """A listing, not a run. An index the route cannot read is one the sweep
    cannot read either, so "not indexed" is what is true at that moment."""
    from workspace_app.api import schedule_index

    client, _, item_id = _app()
    with client:
        _put(client, item_id, ".workflows/nightly.json", _NIGHTLY)
        _put(
            client,
            item_id,
            ".workflows/schedules.json",
            json.dumps({"schedules": [{"every": "hourly", "run": "nightly"}]}),
        )

        def _down(self, item_id: str) -> list[str]:
            raise RuntimeError("index down")

        monkeypatch.setattr(schedule_index.ScheduleIndex, "paths", _down)
        r = client.get(f"{_base(item_id)}/schedules")

    assert r.status_code == 200, r.text
    assert r.json()["indexed"] is False
    assert r.json()["rows"][0]["runnable"] is False


def test_a_file_over_the_deployments_cap_is_reported_as_the_sweep_treats_it() -> None:
    """The sweep refuses the WHOLE file over `max_page_schedules` — none of its
    rows run — with one log line nobody reads. The tool refuses at save, but it
    is not the only writer (`write_file`, `exec`, the file PUT), and a panel that
    listed every row with a "Next: …" would be showing rows as if they will
    fire: the exact thing this route exists to prevent."""
    spec = make_spec()
    runner = ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=runner,
        trigger_check_interval=timedelta(hours=1),
        max_page_schedules=2,
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    client = TestClient(app)
    with client:
        _put(client, item_id, ".workflows/nightly.json", _NIGHTLY)
        _put(
            client,
            item_id,
            ".workflows/schedules.json",
            json.dumps({"schedules": [{"every": "hourly", "run": "nightly"}] * 3}),
        )
        r = client.get(f"{_base(item_id)}/schedules")

    body = r.json()
    assert len(body["rows"]) == 3, "the rows are still shown — a person has to fix the file"
    assert any("over the limit of 2" in p for p in body["problems"]), body["problems"]


def test_a_row_that_is_not_an_object_keeps_its_original_value() -> None:
    """The panel rewrites the file minus one row from `raw`. A substitute
    (`{"_": 5}`) written back in place of `5` is a line the author never typed,
    and its problem text changes under them ("must be an object" → "needs run")."""
    client, _, item_id = _app()
    with client:
        _put(client, item_id, ".workflows/nightly.json", _NIGHTLY)
        _put(
            client,
            item_id,
            ".workflows/schedules.json",
            json.dumps({"schedules": [5, {"every": "hourly", "run": "nightly"}]}),
        )
        r = client.get(f"{_base(item_id)}/schedules")

    bad, good = r.json()["rows"]
    assert bad["raw"] == 5
    assert bad["problems"] and good["run"] == "nightly"


def test_a_file_that_is_not_json_is_a_file_level_problem_not_a_500() -> None:
    client, _, item_id = _app()
    with client:
        _put(client, item_id, ".workflows/schedules.json", "{not json")
        r = client.get(f"{_base(item_id)}/schedules")

    assert r.status_code == 200
    assert r.json()["rows"] == []
    assert r.json()["problems"]
