"""Workflow HTTP routes (#100, manual §14) — POST run / GET runs / poll / stream /
cancel / decisions, driven end-to-end through the real ChatTurnEngine with a
scripted runner against the `playground/echo` workflow profile.

The run is a background task, so these use ``with TestClient(app) as client:`` — that
keeps a persistent event-loop thread alive so the run progresses while the test
thread polls (a bare ``TestClient`` tears its loop down between requests)."""

import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection
from workspace_app.sandbox.mock import MockSandbox


def _app(profile: str = "echo") -> tuple[FastAPI, SpecStar, str]:
    spec = make_spec()
    runner = ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()])
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile=profile))
        .resource_id
    )
    return app, spec, item_id


def _route(app: FastAPI, path: str):
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) == path)


def _base(item_id: str) -> str:
    return f"/a/playground/items/{item_id}"


def _put_input(client: TestClient, item_id: str, payload: str) -> None:
    r = client.put(f"{_base(item_id)}/files/inputs/input.json", content=payload)
    assert r.status_code == 204


def _poll(client: TestClient, item_id: str, run_id: str, want: str, tries: int = 200) -> dict:
    data: dict = {}
    for _ in range(tries):
        r = client.get(f"{_base(item_id)}/runs/{run_id}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] == want:
            return data
        time.sleep(0.02)
    raise AssertionError(f"run never reached {want!r}: last={data}")


def test_profiles_endpoint_flags_workflow():
    app, _spec, _iid = _app()
    with TestClient(app) as client:
        profiles = {p["name"]: p for p in client.get("/a/playground/profiles").json()}
    assert profiles["echo"]["has_workflow"] is True
    assert profiles["echo"]["workflow"]["phases"][0]["id"] == "think"
    assert profiles["default"]["has_workflow"] is False


def test_unknown_app_profiles_404():
    app, _spec, _iid = _app()
    with TestClient(app) as client:
        assert client.get("/a/nope/profiles").status_code == 404


def test_run_to_done_with_result():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 7}')
        r = client.post(f"{_base(item_id)}/run")
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
    assert data["result"] == {"status": "done", "n": 7}
    think = next(p for p in data["phases"] if p["phase"] == "think")
    assert think["status"] == "passed"


def test_failing_step_reports_error_phase_and_reason():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"check_path": "out/missing.json"}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        data = _poll(client, item_id, run_id, "error")
    assert "missing.json" in data["result"]["error"]
    think = next(p for p in data["phases"] if p["phase"] == "think")
    assert think["status"] == "failed"


def test_run_requires_workflow_profile():
    """A non-workflow profile (playground/default) can't be run headlessly."""
    app, _spec, item_id = _app(profile="default")
    with TestClient(app) as client:
        assert client.post(f"{_base(item_id)}/run").status_code == 422


def test_double_active_run_is_rejected():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true}')  # first run parks at the gate
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "awaiting_human")
        assert client.post(f"{_base(item_id)}/run").status_code == 409


def test_run_list_newest_first():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 1}')
        r1 = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, r1, "done")
        r2 = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, r2, "done")
        runs = client.get(f"{_base(item_id)}/runs").json()
    assert [r["run_id"] for r in runs][:2] == [r2, r1]


def test_human_gate_decision_approve_resumes_to_done():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true, "n": 5}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        data = _poll(client, item_id, run_id, "awaiting_human")
        assert data["pending_decision"]["phase"] == "review"
        r = client.post(
            f"{_base(item_id)}/runs/{run_id}/decisions",
            json={"choice": "approve", "input": "ship it"},
        )
        assert r.status_code == 202
        data = _poll(client, item_id, run_id, "done")
    assert data["result"] == {"status": "approved", "n": 5, "note": "ship it"}


def test_human_gate_reject_ends_run():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "awaiting_human")
        client.post(f"{_base(item_id)}/runs/{run_id}/decisions", json={"choice": "reject"})
        data = _poll(client, item_id, run_id, "done")
    assert data["result"]["status"] == "rejected"


def test_decision_on_terminal_run_is_409():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"n": 1}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "done")  # terminal, not awaiting
        r = client.post(f"{_base(item_id)}/runs/{run_id}/decisions", json={"choice": "approve"})
    assert r.status_code == 409


def test_cancel_running_gate_run():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        _put_input(client, item_id, '{"gate": true}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        _poll(client, item_id, run_id, "awaiting_human")
        assert client.post(f"{_base(item_id)}/runs/{run_id}/cancel").status_code == 204
        # cancel on a paused/terminal run is a no-op 204 (Stop is idempotent)
        assert client.post(f"{_base(item_id)}/runs/{run_id}/cancel").status_code == 204


def test_get_unknown_run_is_404():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        assert client.get(f"{_base(item_id)}/runs/nope").status_code == 404


def test_run_with_sandbox_node_and_ingest_commits():
    """Exercises a deterministic sandbox node (credential injected into its env) and
    the in-process ingest + collection_has verify through the real wiring."""
    app, spec, item_id = _app()
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    with TestClient(app) as client:
        _put_input(client, item_id, f'{{"sandbox": true, "ingest": "{cid}"}}')
        run_id = client.post(f"{_base(item_id)}/run").json()["run_id"]
        data = _poll(client, item_id, run_id, "done")
    assert data["result"]["status"] == "done"
    assert data["result"]["landed"] is True  # collection_has verified the ingest landed


def test_capability_ingest_lands_a_doc():
    app, spec, item_id = _app()
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    with TestClient(app) as client:
        r = client.put(f"{_base(item_id)}/files/digest/a.md", content=b"# A\nhello content")
        assert r.status_code == 204
        r = client.post(
            f"{_base(item_id)}/capabilities/ingest",
            json={"collection": cid, "path": "digest/a.md"},
        )
    assert r.status_code == 200 and r.json()["doc_id"]


def test_capability_ingest_unknown_collection_404():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        client.put(f"{_base(item_id)}/files/a.md", content=b"x")
        r = client.post(
            f"{_base(item_id)}/capabilities/ingest",
            json={"collection": "no-such", "path": "a.md"},
        )
    assert r.status_code == 404


def test_capability_ingest_rejects_a_bad_token():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        r = client.post(
            f"{_base(item_id)}/capabilities/ingest",
            json={"collection": "c", "path": "a.md"},
            headers={"X-Workflow-Token": "forged"},
        )
    assert r.status_code == 401


async def test_run_stream_endpoint_returns_an_sse_response():
    """The run stream reuses the item's broadcast SSE (manual §14). Invoke the
    handler directly — its body is the item's (never-ending) event stream, which the
    in-process transport would buffer; we assert the wiring + media type."""
    app, _spec, item_id = _app()
    resp = await _route(app, "/a/{slug}/items/{item_id}/runs/{run_id}/stream")(
        slug="playground", item_id=item_id, run_id="any"
    )
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
