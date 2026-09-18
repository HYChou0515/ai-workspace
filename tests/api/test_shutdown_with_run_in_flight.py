"""Shutdown with a scheduled workflow run still in flight RETURNS.

The real-entry-point shape of the hang that held CI's `api-4` shard open for
hours (P9 and P20 runs of #818), reproduced locally at CI's 2 workers:
`test_offered_workflows.py::test_a_schedule_can_name_a_workflow_the_item_authored`
leaves `with client:` while the run its tick started is still running — on a
loaded runner, always. The lifespan drained the run's turn (cancelled it past
the budget), and the turn engine's worker, cancelled while still awaiting that
turn's teardown, swallowed its own cancellation and parked on `queue.get()`;
asyncio's loop close then waited on it forever. The unit pin is in
`test_turn_resilience.py`; this is the door it was found behind, held open so
the drain is exercised with a run that cannot finish on its own.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from workspace_app.api import MessageDelta, RunDone, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

_NIGHTLY = json.dumps(
    {
        "id": "ignored",
        "title": "Nightly",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "cache": True, "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
)


class _NeverAnswers:
    """A turn that is still running whenever the shutdown comes."""

    async def run(self, content, ctx):  # noqa: ANN001, ANN201
        await asyncio.Event().wait()
        yield MessageDelta(text="never")
        yield RunDone()


def test_shutdown_with_a_scheduled_run_in_flight_returns() -> None:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_NeverAnswers(),  # ty: ignore[invalid-argument-type]
        trigger_check_interval=timedelta(hours=1),
        # The drain gives up on the wedged turn at once; what is under test is
        # that giving up ENDS things rather than parking them.
        shutdown_budget=timedelta(seconds=0.2),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    base = f"/api/a/playground/items/{item_id}"
    client = TestClient(app)
    with client:
        put = client.put(f"{base}/files/.workflows/nightly.json", content=_NIGHTLY)
        assert put.status_code == 204
        rows = json.dumps({"schedules": [{"every": "minutes", "n": 1, "run": "nightly"}]})
        assert (
            client.put(f"{base}/files/.workflows/schedules.json", content=rows).status_code == 204
        )
        assert client.portal is not None
        assert client.portal.call(app.state.user_schedule_sweeper.tick) == 1
        runs = client.get(f"{base}/runs").json()
        assert [r["workflow_id"] for r in runs] == ["nightly"]
        # …and leave with that run in flight. Before the fix this `with`
        # never returned (pytest-timeout is what would have named it).
