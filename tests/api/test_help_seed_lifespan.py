"""#804: the boot-time help seed stores on the API and indexes on the index queue.

`seed_help_collection_best_effort` used to call `Ingestor.ingest` — store AND
index, inline, on every API pod at boot: a chunk + embed pass the upload route
never does in-process (it stores, then enqueues). Content that has not changed
is a no-op either way; content that HAS changed cost every pod its own embedding
pass before it was ready. Now the API only stores; the index worker (or the
all-in-one app's own consumer) does the rest.
"""

from __future__ import annotations

import asyncio

from specstar import QB
from specstar.types import TaskStatus

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.help_collection import HELP_COLLECTION_NAME
from workspace_app.kb.index_jobs import IndexJob
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources import Collection, SourceDoc, make_spec
from workspace_app.resources.kb import EMBED_DIM, DocChunk
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _boot(spec, *, run_consumers: bool) -> TestClient:
    text = HashEmbedder(dim=EMBED_DIM)
    return TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=ScriptedAgentRunner([]),
            kb_embedder=text,
            kb_pipeline=build_doc_pipeline(embedder=text),
            run_consumers=run_consumers,
        )
    )


def _help_docs(spec) -> dict[str, SourceDoc]:
    """The help collection's documents, by resource id."""
    (coll,) = [
        r
        for r in spec.get_resource_manager(Collection).list_resources(QB.all())
        if isinstance(r.data, Collection) and r.data.name == HELP_COLLECTION_NAME
    ]
    cid = coll.info.resource_id
    return {
        r.info.resource_id: r.data
        for r in spec.get_resource_manager(SourceDoc).list_resources(
            (QB["collection_id"] == cid).build()
        )
        if isinstance(r.data, SourceDoc)
    }


def _chunks(spec) -> int:
    return sum(1 for _ in spec.get_resource_manager(DocChunk).list_resources(QB.all()))


def test_a_pure_producer_pod_stores_the_help_docs_and_leaves_indexing_to_the_queue() -> None:
    spec = make_spec(default_user="u")
    with _boot(spec, run_consumers=False):
        docs = _help_docs(spec)
        assert docs and all(d.status == "indexing" for d in docs.values())  # stored, not indexed
        assert _chunks(spec) == 0  # no chunk + embed pass ran on this pod
        pending = [
            r.data
            for r in spec.get_resource_manager(IndexJob).list_resources(QB.all().build())
            if isinstance(r.data, IndexJob)
        ]
        assert {j.status for j in pending} == {TaskStatus.PENDING}
        assert {j.payload.doc_id for j in pending} == set(docs)  # one index job per help doc


def test_an_all_in_one_app_still_ends_up_with_the_help_docs_indexed() -> None:
    """Behaviour unchanged for the default deploy: its own consumer answers."""
    spec = make_spec(default_user="u")
    with _boot(spec, run_consumers=True):

        async def _wait() -> dict[str, SourceDoc]:
            for _ in range(100):  # ~5s budget
                docs = _help_docs(spec)
                if docs and all(d.status == "ready" for d in docs.values()):
                    return docs
                await asyncio.sleep(0.05)
            return _help_docs(spec)

        docs = asyncio.run(_wait())

    assert docs and all(d.status == "ready" for d in docs.values())
    assert _chunks(spec) > 0
