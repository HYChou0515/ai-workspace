"""The cluster sweep runs as a card-gen JOB, not on the API pod.

#506 P8 first ran ``sweep_clusters`` on every API pod's own timer: a full
``ClusterMember`` read per collection plus one embedding call per candidate, on N
pods, with zero traffic — the work that grew the API heap until the pod OOMed. It
belongs beside the finalize-time reconcile it duplicates — same embedder, same
tau, same rows — on the card-gen worker. The API only ever *asks* for a sweep.
"""

from __future__ import annotations

from specstar import QB
from specstar.types import TaskStatus

from workspace_app.kb.card_drafter import NullCardDrafter
from workspace_app.kb.card_gen import CardGenJob, ProposedCard
from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
from workspace_app.kb.card_gen_run import CardGenRunStore
from workspace_app.kb.card_proposal import CardProposalStore
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.reconcile import Reconciler
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember


def _collection(spec) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _done_run_with_unprojected_proposal(spec, cid: str, key: str) -> str:
    """A run that finished before anything clustered its proposal (pre-P6, or a
    build with no embedder): the proposal exists, its ``ClusterMember`` does not."""
    store = CardGenRunStore(spec)
    run_id = store.start(cid, ["d1"])
    CardProposalStore(spec).create_from_proposal(
        cid, run_id, ProposedCard(id="0", keys=[key], title=key)
    )
    store.finish(run_id, status="done")
    return run_id


def _members(spec, cid: str) -> list[ClusterMember]:
    rm = spec.get_resource_manager(ClusterMember)
    return [
        r.data
        for r in rm.list_resources((QB["collection_id"] == cid).build())
        if isinstance(r.data, ClusterMember)
    ]


def _coordinator(spec) -> CardGenCoordinator:
    return CardGenCoordinator(
        spec,
        NullCardDrafter(),
        reconciler=Reconciler(spec, HashEmbedder(dim=EMBED_DIM), cluster_tau=0.9, merge_tau=0.95),
    )


async def test_a_cluster_sweep_job_projects_the_collections_unclustered_proposals():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _done_run_with_unprojected_proposal(spec, cid, "RZ3")
    coord = _coordinator(spec)

    coord.enqueue_cluster_sweep(cid)
    assert _members(spec, cid) == []  # asking is not doing — nothing until a consumer runs
    await coord.aclose()

    assert any(m.kind == "proposal" and m.ref_id == "0" for m in _members(spec, cid))


def _sweep_jobs(spec, cid: str) -> list[CardGenJob]:
    rm = spec.get_resource_manager(CardGenJob)
    return [
        r.data
        for r in rm.list_resources((QB["partition_key"] == cid).build())
        if isinstance(r.data, CardGenJob) and r.data.payload.kind == "cluster_sweep"
    ]


async def test_asking_twice_before_a_consumer_runs_queues_one_sweep():
    """Every API pod asks on its own timer; the store must not run one sweep per pod."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    coord = _coordinator(spec)

    coord.enqueue_cluster_sweep(cid)
    coord.enqueue_cluster_sweep(cid)

    assert len(_sweep_jobs(spec, cid)) == 1
    await coord.aclose()


async def test_a_coordinator_with_no_reconciler_ignores_the_ask():
    """No embedder ⇒ nothing to project with. The ask is dropped at the producer,
    not turned into a job that could only fail on the worker."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _done_run_with_unprojected_proposal(spec, cid, "RZ3")
    coord = CardGenCoordinator(spec, NullCardDrafter())  # reconciler=None

    coord.enqueue_cluster_sweep(cid)
    await coord.aclose()

    assert _sweep_jobs(spec, cid) == []
    assert _members(spec, cid) == []


def _onehot(i: int) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[i] = 1.0
    return v


def _member(spec, cid: str, member_id: str, *, cluster_key: str, vec: list[float]) -> None:
    spec.get_resource_manager(ClusterMember).create_or_update(
        member_id,
        ClusterMember(
            collection_id=cid,
            kind="proposal",
            ref_id=member_id,
            cluster_key=cluster_key,
            norm_key=cluster_key,
            embedding=vec,
        ),
    )


async def test_a_cluster_sweep_job_folds_the_collections_race_split_clusters():
    """The sweep's second half: two keys whose centroids coincide (a parallel-race
    split at finalize time) are one inbox row after the job — at the reconciler's
    merge threshold, the same number the finalize-time reconcile is built with."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _member(spec, cid, "z1", cluster_key="zeta", vec=_onehot(0))
    _member(spec, cid, "z2", cluster_key="zeta", vec=_onehot(0))
    _member(spec, cid, "a1", cluster_key="alpha", vec=_onehot(0))
    coord = _coordinator(spec)

    coord.enqueue_cluster_sweep(cid)
    await coord.aclose()

    assert {m.cluster_key for m in _members(spec, cid)} == {"zeta"}


async def test_the_sweep_folds_at_the_reconcilers_threshold_not_a_number_of_its_own():
    """A threshold no cosine can reach ⇒ nothing folds. Pins that the job reads the
    reconciler's ``merge_tau`` (one knob beside ``cluster_tau``), not a private copy."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _member(spec, cid, "z1", cluster_key="zeta", vec=_onehot(0))
    _member(spec, cid, "a1", cluster_key="alpha", vec=_onehot(0))
    coord = CardGenCoordinator(
        spec,
        NullCardDrafter(),
        reconciler=Reconciler(spec, HashEmbedder(dim=EMBED_DIM), cluster_tau=0.9, merge_tau=1.01),
    )

    coord.enqueue_cluster_sweep(cid)
    await coord.aclose()

    assert {m.cluster_key for m in _members(spec, cid)} == {"zeta", "alpha"}


async def test_the_card_gen_worker_folds_at_the_configured_merge_tau():
    """`kb.cluster.merge_tau` reached the API sweeper before; the worker is where
    the sweep runs now, so the knob has to reach the worker's bundle — through its
    real composition root, or a forgotten kwarg leaves the setting silently dead."""
    import dataclasses

    from workspace_app.config.schema import Settings
    from workspace_app.worker.__main__ import build_bundle

    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _member(spec, cid, "z1", cluster_key="zeta", vec=_onehot(0))
    _member(spec, cid, "a1", cluster_key="alpha", vec=_onehot(0))
    settings = Settings()
    settings = dataclasses.replace(
        settings,
        kb=dataclasses.replace(
            settings.kb, cluster=dataclasses.replace(settings.kb.cluster, merge_tau=1.01)
        ),
    )
    coord = build_bundle(settings, spec).card_gen

    coord.enqueue_cluster_sweep(cid)
    await coord.aclose()

    assert {m.cluster_key for m in _members(spec, cid)} == {"zeta", "alpha"}


def test_a_converged_collection_sweeps_to_a_zero_report():
    """Both halves are idempotent, so the timer can ask again and again."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _done_run_with_unprojected_proposal(spec, cid, "A")
    rec = Reconciler(spec, HashEmbedder(dim=EMBED_DIM), cluster_tau=0.9, merge_tau=0.99)

    first = rec.sweep(cid)
    again = rec.sweep(cid)

    assert (first.backfilled, first.merged) == (1, 0)
    assert (again.backfilled, again.merged) == (0, 0)


class _BoomEmbedder:
    """Explodes on one collection's candidate (a transient embed failure)."""

    dim = EMBED_DIM
    identity = "boom"

    def __init__(self) -> None:
        self._h = HashEmbedder(dim=EMBED_DIM)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if any("BOOM" in t for t in texts):
            raise RuntimeError("embed exploded")
        return self._h.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._h.embed_query(text)


async def test_one_collections_failing_sweep_does_not_hold_up_another():
    """One job per collection: the bad one fails on its own and the good one is
    still projected — the store-wide loop's "continue past a failure" guarantee,
    now given by the queue rather than a try/except around a for-loop."""
    spec = make_spec(default_user="u")
    bad = _collection(spec)
    good = _collection(spec)
    _done_run_with_unprojected_proposal(spec, bad, "BOOM")
    _done_run_with_unprojected_proposal(spec, good, "OK")
    coord = CardGenCoordinator(
        spec,
        NullCardDrafter(),
        reconciler=Reconciler(spec, _BoomEmbedder(), cluster_tau=0.9, merge_tau=0.95),
    )

    coord.enqueue_cluster_sweep(bad)
    coord.enqueue_cluster_sweep(good)
    await coord.aclose()

    assert [m.ref_id for m in _members(spec, good) if m.kind == "proposal"] == ["0"]
    assert _members(spec, bad) == []
    assert [j.status for j in _sweep_jobs(spec, bad)] == [TaskStatus.FAILED]
