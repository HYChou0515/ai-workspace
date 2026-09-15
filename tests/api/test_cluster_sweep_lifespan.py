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

    # …and not at shutdown either. The coordinators' `aclose()` starts a consumer
    # on a coordinator that never consumed, "so it still flushes" — on a pure
    # producer that would run every pending job on this pod as it exits: the very
    # work the pod exists not to do, on every rollout, holding the pod past its
    # grace period. A pure producer leaves the queue exactly as it found it.
    still_pending = {
        cid: [
            r.data.status
            for r in spec.get_resource_manager(CardGenJob).list_resources(
                (QB["partition_key"] == cid).build()
            )
            if isinstance(r.data, CardGenJob) and r.data.payload.kind == "cluster_sweep"
        ]
        for cid in cids
    }
    assert still_pending == {cid: [TaskStatus.PENDING] for cid in cids}
    for cid in cids:
        assert list(rm.list_resources((QB["collection_id"] == cid).build())) == []


def _sweep_jobs(spec, cid: str) -> list[CardGenJob]:
    return [
        r.data
        for r in spec.get_resource_manager(CardGenJob).list_resources(
            (QB["partition_key"] == cid).build()
        )
        if isinstance(r.data, CardGenJob) and r.data.payload.kind == "cluster_sweep"
    ]


def _app(spec, **kw):
    text = HashEmbedder(dim=EMBED_DIM)
    return TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=ScriptedAgentRunner([]),
            kb_embedder=text,
            kb_pipeline=build_doc_pipeline(embedder=text),
            **kw,
        )
    )


async def _until(pred, budget_s: float = 3.0):
    for _ in range(int(budget_s / 0.05)):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


def test_the_ask_is_leased_and_skips_a_deleted_collection() -> None:
    """Two things the ask must do that the coordinator's coalescing cannot:
    take the fleet-wide per-window lease (pods tick on their own clocks, so
    without it every pod's tick lands its own job on the worker), and leave a
    soft-deleted collection alone (`list_resources` returns those too — asked
    every window, forever, for a collection nobody can see)."""
    from workspace_app.workflow.triggers import SpecstarTriggerStore

    spec = make_spec(default_user="u")
    colls = spec.get_resource_manager(Collection)
    live = colls.create(Collection(name="live")).resource_id
    gone = colls.create(Collection(name="gone")).resource_id
    colls.delete(gone)

    with _app(spec, run_consumers=False):
        assert asyncio.run(_until(lambda: bool(_sweep_jobs(spec, live))))
        assert SpecstarTriggerStore(spec).last_window("__scan__:cluster-sweep") != ""
        assert _sweep_jobs(spec, gone) == []


def test_one_collections_bad_ask_does_not_cost_the_others_their_tick() -> None:
    """The producer is per-collection resilient: an enqueue that raises for one
    collection (here, a coordinator that refuses it) still leaves every other
    collection asked for in the same tick."""
    spec = make_spec(default_user="u")
    colls = spec.get_resource_manager(Collection)
    bad = colls.create(Collection(name="bad")).resource_id
    good = colls.create(Collection(name="good")).resource_id
    client = _app(spec, run_consumers=False)
    real = client.app.state.card_gen_coordinator

    class _Refusing:
        def enqueue_cluster_sweep(self, cid: str) -> None:
            if cid == bad:
                raise RuntimeError("refused")
            real.enqueue_cluster_sweep(cid)

    client.app.state.card_gen_coordinator = _Refusing()  # the lifespan reads it per tick
    with client:
        assert asyncio.run(_until(lambda: bool(_sweep_jobs(spec, good))))
        assert _sweep_jobs(spec, bad) == []


def _seed_split_clusters(spec, cid: str) -> None:
    v = [0.0] * EMBED_DIM
    v[0] = 1.0
    rm = spec.get_resource_manager(ClusterMember)
    for mid, key in (("z1", "zeta"), ("z2", "zeta"), ("a1", "alpha")):
        rm.create_or_update(
            mid,
            ClusterMember(
                collection_id=cid,
                kind="proposal",
                ref_id=mid,
                cluster_key=key,
                norm_key=key,
                embedding=v,
            ),
        )


def _keys_after_the_apps_own_sweep(merge_tau: float) -> set[str]:
    """An all-in-one app (the default deploy) sweeps through ITS OWN consumer, i.e.
    through `create_app`'s `kb_cluster_merge_tau` wiring — the half the worker
    test cannot see."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    _seed_split_clusters(spec, cid)
    with _app(spec, kb_cluster_merge_tau=merge_tau):
        assert asyncio.run(
            _until(lambda: [j.status for j in _sweep_jobs(spec, cid)] == [TaskStatus.COMPLETED])
        )
    rm = spec.get_resource_manager(ClusterMember)
    return {
        r.data.cluster_key
        for r in rm.list_resources((QB["collection_id"] == cid).build())
        if isinstance(r.data, ClusterMember)
    }


def test_the_apps_own_sweep_folds_at_the_configured_merge_tau() -> None:
    assert _keys_after_the_apps_own_sweep(1.01) == {"zeta", "alpha"}  # unreachable ⇒ no fold
    assert _keys_after_the_apps_own_sweep(0.5) == {"zeta"}  # the control: reachable ⇒ folds
