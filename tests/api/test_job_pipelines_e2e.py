"""End-to-end over HTTP: the graph and eval pipelines actually RUN (#534/#535).

The auto-route regression test proves the endpoints exist; these prove the
FEATURES work through the deployment surface: POST the auto route (creating the
job row IS the enqueue) → the in-app consumers (run_consumers=true under a real
lifespan, hence `with TestClient`) drive dispatch → split → batch → finalize /
reconcile → the outputs land and are readable back over HTTP. A scripted ILlm
serves every prompt shape both pipelines ask for; the eval retriever is the real
one over HashEmbedder vectors.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from specstar import QB
from specstar.types import Binary, TaskStatus

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk, SourceDoc
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

_TERMINAL = {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}


class _ScriptedLlm(ILlm):
    """Stateless answers keyed on each prompt's own wording — thread-safe under
    parallel batch jobs, and one instance serves BOTH pipelines."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if "surface" in prompt:  # graph: the one joint extraction (#630 P4)
            yield (
                '{"mentions": [{"surface": "回焊爐", "kind": "機台"}],'
                ' "aliases": [], "relationships": [],'
                ' "attributes": [{"subject": "回焊爐", "attribute": "Yield",'
                ' "period": "Q3", "value": "98.7", "unit": "%"}]}',
                False,
            )
        elif "just 'yes' or 'no'" in prompt:  # eval: answerability filter
            yield "yes", False
        elif "question that this passage directly answers" in prompt:  # eval: generate
            yield "What does this passage measure?", False
        else:  # graph: vocabulary reconcile (or future asks) — harmless empty
            yield "[]", False


def _seed(spec, *, use_graph: bool) -> str:
    """A collection with one real SourceDoc + chunks — enough for both pipelines."""
    coll_id = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="corpus", use_graph=use_graph))
        .resource_id
    )
    drm = spec.get_resource_manager(SourceDoc)
    crm = spec.get_resource_manager(DocChunk)
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=coll_id,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            ),
            resource_id="deck-1",
        )
    for i in range(3):
        crm.create(
            DocChunk(
                collection_id=coll_id,
                source_doc_id="deck-1",
                seq=i,
                start=i,
                end=i + 1,
                text=f"Passage {i}: the reflow oven yield was 98.7% in Q3.",
                embedding=HashEmbedder(dim=EMBED_DIM).embed_documents([f"passage {i}"])[0],
            )
        )
    return coll_id


def _app(spec):
    from workspace_app.api import create_app

    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=None,  # ty: ignore[invalid-argument-type] — no chat turn here
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=1),
        kb_llm=_ScriptedLlm(),
    )


def _drain(client: TestClient, model: str, *, deadline_s: float = 180.0) -> list[dict]:
    """Poll the auto route until every job row is terminal. Under `with
    TestClient` the app's event loop runs continuously on the portal thread, so
    the consumers progress during the real sleeps — time-based (not
    round-based) because a loaded CI runner legitimately needs minutes where a
    laptop needs seconds (the first CI run flaked exactly there)."""
    deadline = time.monotonic() + deadline_s
    rows: list[dict] = []
    while time.monotonic() < deadline:
        rows = client.get(f"/api/{model}/data").json()
        if rows and all(r["status"] in _TERMINAL for r in rows):
            return rows
        time.sleep(0.25)
    raise AssertionError(f"{model} jobs never drained: {rows}")


def test_graph_pipeline_runs_end_to_end_over_http():
    spec = make_spec()
    coll_id = _seed(spec, use_graph=True)
    with TestClient(_app(spec)) as client:  # `with` — consumers live on the lifespan
        # POST on the auto route IS the enqueue — the trigger the user expected.
        resp = client.post("/api/graph-job", json={"payload": {"kind": "dispatch"}})
        assert resp.status_code < 300, resp.text

        rows = _drain(client, "graph-job")
        # dispatch fanned out: dispatch + one split per opted-in collection +
        # batches + the reconcile tail — and EVERY one of them finished OK.
        assert len(rows) >= 3
        assert all(r["status"] == TaskStatus.COMPLETED.value for r in rows), rows

    # The extraction actually landed: claims for the seeded collection…
    from workspace_app.resources.graph import GraphClaim

    claims = [
        r.data
        for r in spec.get_resource_manager(GraphClaim).list_resources(
            (QB["collection_id"] == coll_id).build()
        )
        if isinstance(r.data, GraphClaim)  # narrow Struct|Unset for ty
    ]
    assert claims, "no GraphClaim rows — the batch stage did nothing"
    assert {c.attribute for c in claims} == {"Yield"}
    # #630: and it knows WHOSE figure it is, not just that a slide had one
    assert {c.subject for c in claims} == {"回焊爐"}


def test_eval_pipeline_runs_end_to_end_over_http():
    spec = make_spec()
    coll_id = _seed(spec, use_graph=False)
    with TestClient(_app(spec)) as client:
        resp = client.post(
            "/api/eval-job",
            json={"payload": {"kind": "dispatch", "run_label": "e2e", "sample_size": 3}},
        )
        assert resp.status_code < 300, resp.text

        rows = _drain(client, "eval-job")
        assert all(r["status"] == TaskStatus.COMPLETED.value for r in rows), rows

        # Every run joined + finalized (the dispatch sweeps EVERY collection,
        # including the boot-seeded system ones — an empty collection correctly
        # degrades to a zero-row rather than wedging its run).
        runs = client.get("/api/eval-run/data").json()
        assert runs and all(r["status"] == "done" for r in runs), runs
        # …and the metrics are readable back over the auto route that always
        # existed. Sanity for every row, exactness for the seeded corpus.
        results = client.get("/api/eval-result/data").json()
        assert results, "no EvalResult rows — finalize never ran"
        for res in results:
            assert res["run_label"] == "e2e"
            assert 0.0 <= res["mrr_chunk"] <= 1.0
            assert all(0.0 <= v <= 1.0 for v in res["recall_chunk"].values())
        mine = next(r for r in results if r["collection_id"] == coll_id)
        assert mine["n_generated"] == 3  # one question per sampled chunk
        assert mine["n_kept"] == 3  # the yes-filter kept them all
        assert mine["mrr_doc"] == 1.0  # the one source doc came back at rank 1
