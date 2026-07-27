"""#534 — the manual "extract now" path for one collection.

The dispatch cronjob runs WEEKLY (``0 3 * * 6``, Asia/Taipei), so a collection
that has just been opted in would otherwise sit untouched for up to seven days.
This route queues that collection's own ``split`` immediately.

It is accept-and-return, the same shape as the wiki rebuild (#571) and the
collection re-read (#569): the request enqueues one job and answers. The fake
LLM here raises if it is ever called, so a route that tried to extract inline
would fail these tests rather than merely be slow.
"""

from __future__ import annotations

from specstar import QB, SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.graph.jobs import GraphJob
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _FakeLlm:
    def complete(self, *a, **k):  # pragma: no cover — wiring only
        raise AssertionError("the rebuild route must not extract inline")


def _client_and_spec(holder: dict[str, str]) -> tuple[TestClient, SpecStar]:
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        kb_llm=_FakeLlm(),  # ty: ignore[invalid-argument-type] — wires the graph coordinator
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _collection(spec: SpecStar, *, use_graph: bool, private: bool = False) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        return crm.create(
            Collection(
                name="c",
                use_graph=use_graph,
                permission=Permission(visibility="private") if private else None,
            )
        ).resource_id


def _split_jobs(spec: SpecStar, collection_id: str) -> list:
    """The collection's own queued work. ``partition_key`` is indexed, so this
    is the same lookup the queue itself does — not a scan."""
    jrm = spec.get_resource_manager(GraphJob)
    jobs = [
        jrm.get(m.resource_id).data
        for m in jrm.search_resources((QB["partition_key"] == collection_id).build())
    ]
    return [
        j for j in jobs if j.payload.kind == "split" and j.payload.collection_id == collection_id
    ]


def test_rebuild_queues_one_split_for_the_collection():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = _collection(spec, use_graph=True)

    resp = client.post(f"/kb/collections/{cid}/graph/rebuild")

    assert resp.status_code == 200
    assert resp.json()["status"] == "rebuilding"
    assert len(_split_jobs(spec, cid)) == 1


def test_rebuild_partitions_on_the_collection():
    """``partition_key`` = the collection, so two presses serialise instead of
    fanning the same docs out twice in parallel."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = _collection(spec, use_graph=True)

    client.post(f"/kb/collections/{cid}/graph/rebuild")

    (job,) = _split_jobs(spec, cid)
    assert job.partition_key == cid


def test_rebuild_is_a_no_op_when_the_collection_has_not_opted_in():
    """Extraction is expensive VLM/LLM work — a collection that never asked for
    it must not get it because someone pressed a button."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = _collection(spec, use_graph=False)

    resp = client.post(f"/kb/collections/{cid}/graph/rebuild")

    assert resp.status_code == 200
    assert resp.json() == {"queued": 0, "status": "disabled"}
    assert _split_jobs(spec, cid) == []


def test_rebuild_404s_on_an_unknown_collection():
    holder = {"id": "bob"}
    client, _ = _client_and_spec(holder)
    assert client.post("/kb/collections/nope/graph/rebuild").status_code == 404


def test_rebuild_is_refused_without_edit_content():
    """Same gate as the wiki rebuild (#607) — queueing a corpus-wide extraction
    is a content operation, not a read."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    cid = _collection(spec, use_graph=True, private=True)

    holder["id"] = "mallory"
    assert client.post(f"/kb/collections/{cid}/graph/rebuild").status_code in (403, 404)
    assert _split_jobs(spec, cid) == []
