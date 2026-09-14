"""#506 P8: the cluster sweeper is actually wired into the app lifespan.

When the app boots, a background task asks for every collection's cluster sweep —
a ``cluster_sweep`` card-gen job that backfills any pending proposal with no
:class:`ClusterMember` yet (e.g. a run finalized before P6) so the grouped 待審核
inbox can cluster it. An all-in-one app (consumers on) answers its own ask; a
pure-producer pod leaves the job for the card-gen worker and does none of the
work itself — the sweep on the API pod is what grew its heap until it OOMed.
"""

from __future__ import annotations

import asyncio

from specstar import QB
from specstar.types import TaskStatus

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.card_gen import CardGenJob, ProposedCard
from workspace_app.kb.card_gen_run import CardGenRunStore
from workspace_app.kb.card_proposal import CardProposalStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def test_lifespan_runs_the_cluster_sweeper() -> None:
    spec = make_spec(default_user="u")
    text = HashEmbedder(dim=EMBED_DIM)
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    CardProposalStore(spec).create_from_proposal(
        cid, run_id, ProposedCard(id="0", keys=["RZ3"], title="RZ3")
    )
    store.finish(run_id, status="done")  # a done run whose proposal has no member

    application = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=text,
        kb_pipeline=build_doc_pipeline(embedder=text),
    )
    client = TestClient(application)
    with client:  # enter lifespan → the cluster sweeper ticks at startup

        async def _wait() -> list[ClusterMember]:
            rm = spec.get_resource_manager(ClusterMember)
            for _ in range(40):  # ~2s budget
                rows = [
                    r.data
                    for r in rm.list_resources((QB["collection_id"] == cid).build())
                    if isinstance(r.data, ClusterMember)
                ]
                if rows:
                    return rows
                await asyncio.sleep(0.05)
            return []

        members = asyncio.run(_wait())

    assert any(m.kind == "proposal" and m.ref_id == "0" for m in members)


def test_a_pure_producer_pod_asks_for_the_sweep_and_does_none_of_it() -> None:
    """The OOM: every API pod ran the sweep itself — every member of every
    collection read into the heap plus one embedding call per candidate, on a
    timer, with zero traffic. A pod with its consumers off (the #312 pod-split
    deploy) must now only *enqueue* the collection's ``cluster_sweep`` job for the
    card-gen worker and touch no ``ClusterMember`` of its own."""
    spec = make_spec(default_user="u")
    text = HashEmbedder(dim=EMBED_DIM)
    colls = spec.get_resource_manager(Collection)
    cids = [colls.create(Collection(name=n)).resource_id for n in ("kb1", "kb2")]
    store = CardGenRunStore(spec)
    for cid in cids:
        run_id = store.start(cid, ["d1"])
        CardProposalStore(spec).create_from_proposal(
            cid, run_id, ProposedCard(id="0", keys=["RZ3"], title="RZ3")
        )
        store.finish(run_id, status="done")

    application = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=text,
        kb_pipeline=build_doc_pipeline(embedder=text),
        run_consumers=False,
    )
    client = TestClient(application)
    with client:

        async def _wait_for_jobs() -> dict[str, list[CardGenJob]]:
            rm = spec.get_resource_manager(CardGenJob)
            for _ in range(40):  # ~2s budget
                jobs = {
                    cid: [
                        r.data
                        for r in rm.list_resources((QB["partition_key"] == cid).build())
                        if isinstance(r.data, CardGenJob) and r.data.payload.kind == "cluster_sweep"
                    ]
                    for cid in cids
                }
                if all(jobs.values()):
                    return jobs
                await asyncio.sleep(0.05)
            return {}

        jobs = asyncio.run(_wait_for_jobs())
        # every collection asked for, once — and nobody here answers
        assert {cid: [j.status for j in js] for cid, js in jobs.items()} == {
            cid: [TaskStatus.PENDING] for cid in cids
        }
        rm = spec.get_resource_manager(ClusterMember)
        for cid in cids:
            assert list(rm.list_resources((QB["collection_id"] == cid).build())) == []
