"""Standalone job worker (#312): `python -m workspace_app.worker <jobtype>`.

A worker pod builds the same coordinator bundle the API builds, then
block-consumes ONE JobType so it can scale under its own k8s HPA. These tests
pin the two pure seams — picking the coordinator for a jobtype, and the
consume-until-stopped loop that drains on shutdown — without spinning up the
full factory stack.
"""

from __future__ import annotations

import threading

import pytest
from specstar.types import Binary

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.api import ScriptedAgentRunner
from workspace_app.coordinators import build_coordinators
from workspace_app.resources import Collection, SourceDoc, make_spec
from workspace_app.worker import (
    _JOBTYPE_ATTR,
    API_REGISTRY_JOBTYPES,
    consume_until_stopped,
    select_coordinator,
)


class _FakeIngestor:
    def __init__(self) -> None:
        self.indexed: list[str] = []

    def index(
        self, doc_id: str, *, source_doc_rm: object | None = None, reraise: bool = False
    ) -> None:
        self.indexed.append(doc_id)


def _bundle(spec, ingestor, **overrides):
    kwargs = dict(
        ingestor=ingestor,
        runner=ScriptedAgentRunner([]),
        catalog=AgentConfigCatalog(),
        message_queue_factory=None,
        get_user_id=lambda: "u",
        quality_judge_llm=None,
        card_drafter_llm=None,
        sanity_llm_factory=None,
        sanity_judge_llm=None,
        wiki_maintainer_max_turns=40,
    )
    kwargs.update(overrides)
    return build_coordinators(spec, **kwargs)  # ty: ignore[invalid-argument-type]


def test_select_coordinator_maps_each_jobtype_to_its_coordinator():
    """Every jobtype a pod can be started with, not a sample of them.

    This asserted three of seven while claiming "each", so a new JobType could be
    added — as #715's `kb-import` was — and reach production with its `workers.yaml`
    Deployment pointing at a name nothing had ever resolved. The expected mapping is
    written out rather than read from `_JOBTYPE_ATTR`, or the test would just be
    that dict compared with itself; the exhaustiveness check below is what keeps the
    two from drifting apart.
    """
    bundle = _bundle(
        make_spec(default_user="u"),
        _FakeIngestor(),
        sanity_llm_factory=lambda *a, **k: None,
        # `eval` and `graph` are LLM-gated; a placeholder is enough to build them,
        # because what is under test is the NAME → coordinator mapping, not the model.
        eval_llm=object(),
        graph_llm=object(),
    )
    expected = {
        "index": bundle.index,
        "wiki": bundle.wiki,
        "card-gen": bundle.card_gen,
        "sanity": bundle.sanity,
        "eval": bundle.eval,
        "graph": bundle.graph,
        "kb-import": bundle.kb_import,
        "blob-gc": bundle.blob_gc,
    }
    assert set(expected) == set(_JOBTYPE_ATTR), (
        "a jobtype was added or renamed without a case here — a worker pod can be "
        "started with a name this test has never resolved"
    )
    for jobtype, coordinator in expected.items():
        assert coordinator is not None, f"{jobtype} is unwired — the case below proves nothing"
        assert select_coordinator(bundle, jobtype) is coordinator, jobtype


def test_select_coordinator_rejects_an_unknown_jobtype():
    bundle = _bundle(make_spec(default_user="u"), _FakeIngestor())
    with pytest.raises(ValueError, match="bogus"):
        select_coordinator(bundle, "bogus")


def test_select_coordinator_errors_when_sanity_is_unwired():
    # No sanity_llm_factory ⇒ bundle.sanity is None ⇒ nothing to consume.
    bundle = _bundle(make_spec(default_user="u"), _FakeIngestor())
    with pytest.raises(ValueError, match="sanity"):
        select_coordinator(bundle, "sanity")


def test_consume_until_stopped_drains_in_flight_jobs_then_stops():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    doc_id = (
        spec.get_resource_manager(SourceDoc)
        .create(
            SourceDoc(collection_id=cid, path="a.md", content=Binary(data=b"x"), status="indexing")
        )
        .resource_id
    )
    ing = _FakeIngestor()
    bundle = _bundle(spec, ing)
    bundle.index.enqueue(doc_id, cid)

    stop = threading.Event()
    stop.set()  # already-stopped: start consuming, then drain + stop on shutdown
    consume_until_stopped(bundle.index, stop)

    assert ing.indexed == [doc_id]  # the in-flight job drained before exit
    assert not bundle.index.consuming  # consumer torn down on stop


def test_build_bundle_forwards_the_context_knobs_to_the_eval_retriever(tmp_path):
    """The worker's composition root builds its own Retriever(s) from settings —
    the door where `context_chars: null` crashed (P7). The knobs it reads must
    be the ones the operator set."""
    from textwrap import dedent

    from workspace_app.config.loader import load
    from workspace_app.worker.__main__ import build_bundle

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval:
                context_chars: 7
                rerank_context_chars: 0
        """),
        encoding="utf-8",
    )
    settings = load(config_path=cfg, env={})
    bundle = build_bundle(settings, make_spec(default_user="u"))
    assert bundle.eval is not None
    r = bundle.eval._retriever
    assert r is not None and (r._context_chars, r._rerank_context_chars) == (7, 0)


# ── blob-gc: the one worker that must hold the API's WHOLE registry ──────────
#
# specstar's blob reconcile builds its live set from the REGISTERED models, and
# any `list` / `dict` / union / `Optional` field gets a runtime blob collector
# (only an all-scalar struct is skipped) — so the scanned set is nearly every
# model, registered all over `create_app`
# (`workspace-file` by the filestore, `-sandboxactivity` by the sandbox layer,
# `conversation-todos` by a route module, …). A consumer holding fewer would
# read every blob the missing models reference as an orphan and delete it after
# t2 (#804 P4). So the blob-gc worker consumes from the API's own composition.


def test_the_blob_gc_worker_is_built_from_the_apis_composition():
    assert set(_JOBTYPE_ATTR) >= API_REGISTRY_JOBTYPES
    assert "blob-gc" in API_REGISTRY_JOBTYPES


def test_the_blob_gc_worker_holds_every_model_the_apis_ask_names(tmp_path, monkeypatch):
    """Both real doors: the API that ASKS (an app whose lifespan ran — that is
    where `_ScheduleIndex`, `_TriggerWindow`, the address store and the stretch
    claims used to be registered — and whose sweeper produced the actual
    `BlobGcJob`) and the worker that RUNS (`build_coordinator`, what `main`
    calls), fed that job's own `registry` claim. An oracle that never entered
    the lifespan was green over a worker that would refuse every pass in a
    pod-split deploy. The oracle must contain the model whose absence was the
    first danger, or a green run proves only that two incomplete registries
    agree. Reddens when blob-gc is routed through `build_bundle` (no filestore,
    no sandbox layer, no routes) — the state #804 P4 refused to ship."""
    import asyncio
    from datetime import timedelta

    from specstar import QB

    from workspace_app.api import create_app
    from workspace_app.config.loader import load
    from workspace_app.config.schema import OffHoursSettings
    from workspace_app.factories import get_filestore, get_spec
    from workspace_app.filestore.blob_gc import BlobGcJob
    from workspace_app.sandbox.mock import MockSandbox
    from workspace_app.worker.__main__ import build_coordinator

    from .api._client import TestClient

    cfg = tmp_path / "config.yaml"
    cfg.write_text("filestore:\n  kind: specstar\n", encoding="utf-8")
    settings = load(config_path=cfg, env={})
    # Tool packages are prebuilt bundles the API refuses to boot without; the
    # explicit opt-out (an empty PACKAGES) is the documented way to run without.
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})

    oracle = get_spec(settings)
    app = create_app(
        spec=oracle,
        sandbox=MockSandbox(),
        filestore=get_filestore(settings, oracle),
        runner=ScriptedAgentRunner([]),
        run_consumers=False,  # a pure producer: the ask is a row, not a pass
        gc_interval=timedelta(seconds=0.05),
        goal_offhours=OffHoursSettings(window="22:00-06:00"),  # the conditional one
    )
    rm = oracle.get_resource_manager(BlobGcJob)

    def _ask():
        return [r.data for r in rm.list_resources(QB.all().build())]

    async def _until_asked():
        for _ in range(60):
            if _ask():
                return True
            await asyncio.sleep(0.05)
        return bool(_ask())

    before_lifespan = set(oracle.resource_managers)
    with TestClient(app):
        assert asyncio.run(_until_asked()), "the API's sweeper never asked"
        (job,) = _ask()
    api_models = set(oracle.resource_managers)
    # The lifespan registers nothing: a model it added would be one the worker
    # (which never enters one) lacks — the state the first version of this
    # test was green over.
    assert api_models == before_lifespan
    assert "workspace-file" in api_models
    assert set(job.payload.registry) == api_models  # the ask names what the API holds

    coordinator = build_coordinator(settings, "blob-gc", config_dir=None)
    worker_models = set(coordinator._spec.resource_managers)  # ty: ignore[unresolved-attribute]
    assert api_models <= worker_models, (
        f"the blob-gc worker cannot see {sorted(api_models - worker_models)}: a pass on "
        "it would quarantine, then delete, every blob those models reference"
    )
    coordinator._check_registry(job.payload.registry)  # ty: ignore[unresolved-attribute]
