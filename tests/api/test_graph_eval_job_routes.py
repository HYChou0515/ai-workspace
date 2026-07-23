"""The graph-job / eval-job endpoints — the ignition the engines never had.

#534 (graph) and #535 (eval) shipped fan-out coordinators, worker jobtypes and
k8s worker deployments — but `enqueue_dispatch` had ZERO callers: no route, no
sweeper. The jobs could never start in any deployment (the user found GraphJob
in openapi.json's schemas with no endpoint using it). These routes are the
producers: fire a run, see the jobs, read the results.
"""

from __future__ import annotations

from specstar import QB

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.graph.jobs import GraphJob
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _FakeLlm:
    """The ILlm surface the producer path never calls — wiring only."""

    def complete(self, *a, **k):  # pragma: no cover — never invoked by producers
        raise AssertionError("producer routes must not call the LLM")


def _client_and_spec(holder, *, superusers=frozenset(), kb_llm=True):
    from workspace_app.api import create_app

    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=None,  # ty: ignore[invalid-argument-type] — no turn is driven here
        kb_llm=_FakeLlm() if kb_llm else None,  # ty: ignore[invalid-argument-type]
        superusers=superusers,
    )
    return TestClient(app), spec


def test_superuser_fires_a_graph_run_and_a_dispatch_job_lands():
    holder = {"id": "root"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))

    resp = client.post("/api/kb/graph/run")

    assert resp.status_code == 200
    assert resp.json()["status"] == "dispatched"
    rm = spec.get_resource_manager(GraphJob)
    jobs = [r.data for r in rm.list_resources(QB.all().build())]
    assert len(jobs) == 1
    assert jobs[0].payload.kind == "dispatch"


def test_a_regular_user_cannot_fire_a_graph_run():
    holder = {"id": "alice"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))

    assert client.post("/api/kb/graph/run").status_code == 403
    rm = spec.get_resource_manager(GraphJob)
    assert rm.count_resources(QB.all().build()) == 0


def test_graph_run_reports_disabled_when_no_llm_is_wired():
    holder = {"id": "root"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}), kb_llm=False)

    resp = client.post("/api/kb/graph/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


def test_a_second_press_coalesces_while_a_pass_is_still_running():
    """#571 anti-mash lesson: while any graph job is still queued/processing,
    another press must not stack a second full pass — report already_running."""
    holder = {"id": "root"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))

    assert client.post("/api/kb/graph/run").json()["status"] == "dispatched"
    resp = client.post("/api/kb/graph/run")
    assert resp.json()["status"] == "already_running"
    rm = spec.get_resource_manager(GraphJob)
    assert rm.count_resources(QB.all().build()) == 1


def test_graph_jobs_overview_counts_by_status():
    """The GraphJob schema finally gets an endpoint: counts per status so an
    operator can see a pass moving (or stuck) without kubectl."""
    from specstar.types import TaskStatus

    holder = {"id": "root"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}))
    client.post("/api/kb/graph/run")

    resp = client.get("/api/kb/graph/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["counts"] == {TaskStatus.PENDING.value: 1}


def test_superuser_fires_an_eval_run_and_a_dispatch_job_lands():
    from workspace_app.kb.eval.jobs import EvalJob

    holder = {"id": "root"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))

    resp = client.post("/api/kb/eval/run", json={"run_label": "baseline-1", "sample_size": 20})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "dispatched"
    assert body["run_label"] == "baseline-1"
    rm = spec.get_resource_manager(EvalJob)
    jobs = [r.data for r in rm.list_resources(QB.all().build())]
    assert len(jobs) == 1
    assert jobs[0].payload.kind == "dispatch"


def test_eval_run_defaults_the_label_and_gates_like_graph():
    holder = {"id": "root"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}))
    body = client.post("/api/kb/eval/run", json={}).json()
    assert body["status"] == "dispatched"
    assert body["run_label"]  # a server-defaulted, non-empty label

    holder["id"] = "alice"
    assert client.post("/api/kb/eval/run", json={}).status_code == 403


def test_eval_run_reports_disabled_when_no_llm_is_wired():
    holder = {"id": "root"}
    client, _ = _client_and_spec(holder, superusers=frozenset({"root"}), kb_llm=False)
    assert client.post("/api/kb/eval/run", json={}).json()["status"] == "disabled"


def test_eval_runs_and_results_are_readable():
    """The other half of #535's gap: results existed in the store with no way
    to read them. Runs = fan-out join state (progress); results = the metrics."""
    from workspace_app.resources.eval import EvalResult, EvalRun, eval_run_id

    holder = {"id": "root"}
    client, spec = _client_and_spec(holder, superusers=frozenset({"root"}))
    run_rm = spec.get_resource_manager(EvalRun)
    run_rm.create(
        EvalRun(collection_id="c1", run_label="base", total=3, done=[0, 1], status="running"),
        resource_id=eval_run_id("c1", "base"),
    )
    res_rm = spec.get_resource_manager(EvalResult)
    res_rm.create(
        EvalResult(
            collection_id="c1",
            run_label="base",
            n_kept=42,
            recall_chunk={"5": 0.8},
            mrr_chunk=0.61,
        )
    )

    runs = client.get("/api/kb/eval/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["collection_id"] == "c1"
    assert runs[0]["status"] == "running"
    assert runs[0]["done"] == 2 and runs[0]["total"] == 3

    results = client.get("/api/kb/eval/results").json()["results"]
    assert len(results) == 1
    assert results[0]["run_label"] == "base"
    assert results[0]["recall_chunk"] == {"5": 0.8}
    assert results[0]["mrr_chunk"] == 0.61
