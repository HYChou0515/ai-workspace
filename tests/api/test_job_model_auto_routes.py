"""Job models get their specstar auto-CRUD routes (the ordering bug).

`spec.apply(...)` generates routes for the models registered AT THAT POINT —
but `build_coordinators` (which `add_model`s every job model: index, wiki,
card-gen, graph, eval) ran AFTER it. Net effect: the schemas appeared in
openapi.json with no endpoints using them (the user's exact observation for
graph-job / eval-job), and the standard specstar surface — POST /api/graph-job
IS the enqueue, GET lists the rows — never existed for any job family.
"""

from __future__ import annotations

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _FakeLlm:
    def complete(self, *a, **k):  # pragma: no cover — wiring only
        raise AssertionError("never called")


def _openapi_paths() -> dict:
    from workspace_app.api import create_app

    app = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=None,  # ty: ignore[invalid-argument-type]
        kb_llm=_FakeLlm(),  # ty: ignore[invalid-argument-type] — wires eval+graph
    )
    client = TestClient(app)
    return client.get("/api/openapi.json").json()["paths"]


def test_every_job_model_has_its_auto_route():
    paths = _openapi_paths()
    # The two the user hit first — and the rest of the job family, which had
    # silently shared the same fate.
    for model in (
        "graph-job",  # the two the user hit first…
        "eval-job",
        "index-job",  # …and the rest of the family, silently sharing the fate
        "card-gen-job",
        "wiki-maintenance-job",
    ):
        assert f"/api/{model}" in paths, f"no auto route for {model}"
        # POST on the collection IS the enqueue (a job row is the queue item),
        # so the trigger endpoint exists by construction — no hand-rolled one.
        assert "post" in paths[f"/api/{model}"]
