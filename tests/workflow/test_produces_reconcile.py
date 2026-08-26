"""Reconciling the sandbox is a `produces` node's need, not every turn's tax (#428 §1).

A turn's shell writes land in the sandbox; `wf.glob` reads the durable snapshot. #727
bridged them by writing back after EVERY workflow turn — correct, and measurably
expensive: the write-back walks the workspace, so its cost scales with the number of
files and a map pays it once per element. Measured on the real path with a real
LocalProcessSandbox, 20 agent map elements:

    400 files in the workspace   141 ms/element → 191 ms/element   (+50 ms)
    1600 files in the workspace          —      → 356 ms/element  (+215 ms)

A thousand-element map over a sixteen-hundred-file workspace is minutes of pure mirror.
Only a `produces` node reads the snapshot the instant its turn ends, so only it needs
the write-back; everything else goes back to the periodic sweep it always relied on.
"""

import json
from typing import Any

from tests.api._client import TestClient
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _app() -> tuple[Any, str]:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="done"), RunDone()]),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    return app, item_id


_PROSE = {
    "id": "flow",
    "phases": [{"id": "p"}],
    "steps": [{"type": "agent", "phase": "p", "prompt": "write it", "out": "note.md"}],
}

_PRODUCES = {
    "id": "flow",
    "phases": [{"id": "p"}],
    "steps": [
        {
            "type": "agent",
            "name": "fetch",
            "phase": "p",
            "tools": ["exec"],
            "produces": "data/*.json",
            "prompt": "write the files",
        }
    ],
}


def _spy_flush(monkeypatch) -> list[str]:
    from workspace_app.api.registry import InvestigationRegistry

    seen: list[str] = []
    original = InvestigationRegistry.flush

    async def spy(self, investigation_id: str) -> None:
        seen.append(investigation_id)
        await original(self, investigation_id)

    monkeypatch.setattr(InvestigationRegistry, "flush", spy)
    return seen


def _run(client: TestClient, item_id: str, wf: dict, want: str) -> dict:
    import time

    base = f"/a/playground/items/{item_id}"
    assert (
        client.put(f"{base}/files/.workflows/flow.json", content=json.dumps(wf)).status_code == 204
    )
    run_id = client.post(f"{base}/run?workflow_id=flow").json()["run_id"]
    for _ in range(400):
        data = client.get(f"{base}/runs/{run_id}").json()
        if data["status"] == want:
            return data
        time.sleep(0.02)
    raise AssertionError(f"run never reached {want!r}: last={data}")


def test_a_produces_node_reconciles_before_it_globs(monkeypatch):
    """Its gate reads the snapshot the moment the turn ends, so the write-back has to
    have happened — otherwise a step that did its work fails its own gate."""
    seen = _spy_flush(monkeypatch)
    app, item_id = _app()
    with TestClient(app) as client:
        # the node writes nothing, so the gate fails — the reconcile still has to run,
        # which is what this asserts (the glob has to look at fresh state to be wrong).
        _run(client, item_id, _PRODUCES, "error")
    assert item_id in seen


def test_an_ordinary_turn_does_not_pay_for_it(monkeypatch):
    """A prose node's artifact is written by the engine, not by a shell — it has nothing
    to reconcile, and the write-back walks the whole workspace."""
    seen = _spy_flush(monkeypatch)
    app, item_id = _app()
    with TestClient(app) as client:
        _run(client, item_id, _PROSE, "done")
    assert seen == []
