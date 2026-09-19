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
        "chat-video": bundle.chat_video,
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


def test_the_chat_video_worker_is_built_from_the_apis_composition(tmp_path, monkeypatch):
    """plan-chat-video-export P5: the video job writes through the API's
    `WorkspaceFiles` (quota, jail, mirror), which only `create_app` composes —
    so this worker, like blob-gc, boots `build_app` and takes the coordinator
    the API wired, files and all. Through the worker's real door
    (`build_coordinator`, what `main` calls): the coordinator it hands back
    already holds a facade — routed through `build_bundle` it would hold none
    and every job would fail at `files`."""
    from textwrap import dedent

    from workspace_app.chat_video.jobs import ChatVideoCoordinator
    from workspace_app.config.loader import load
    from workspace_app.files import WorkspaceFiles
    from workspace_app.worker.__main__ import build_coordinator

    assert "chat-video" in API_REGISTRY_JOBTYPES
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(f"""
            server:
              run_consumers: false
              superusers: [root]
            filestore:
              kind: specstar
            sandbox:
              root: {tmp_path / "sandbox"}
            chat_video:
              max_output_bytes: 12345
            kb:
              embedder:
                model: ""
        """),
        encoding="utf-8",
    )
    settings = load(config_path=cfg, env={})
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})

    coordinator = build_coordinator(settings, "chat-video", config_dir=None)

    assert isinstance(coordinator, ChatVideoCoordinator)
    assert isinstance(coordinator.files, WorkspaceFiles)
    assert coordinator._limits.max_output_bytes == 12345  # the configmap reaches the worker
    assert coordinator._superusers == frozenset({"root"})  # and so does the superuser set


def test_the_blob_gc_worker_holds_every_model_the_apis_ask_names(tmp_path, monkeypatch):
    """The production shape, both sides through their real doors: the API that
    ASKS is `workspace_app.__main__.build_app` with its lifespan run under
    `TestClient` as a pure producer, so its sweeper writes the actual
    `BlobGcJob`; the worker that RUNS is `worker.build_coordinator` (what
    `main` calls). The two registries must be EQUAL and the row's own
    `registry` claim must pass the worker's `_check_registry`.

    The first version's oracle was a bare `create_app` that never entered the
    lifespan — green over a worker that refused every pass in a pod-split
    deploy, because four models were registered only inside the lifespan
    (`_ScheduleIndex`, `_TriggerWindow`, and, when on, `_SandboxAddress` /
    `_GoalStretch`). Two incomplete registries agreeing. Hence: the oracle is
    the asker, the lifespan must add nothing, and off-hours is on so the
    once-conditional registration is exercised. Reddens when blob-gc is routed
    through `build_bundle` (no filestore, no sandbox layer, no routes) — the
    state #804 P4 refused to ship."""
    import asyncio
    from textwrap import dedent

    from specstar import QB

    from workspace_app.__main__ import build_app
    from workspace_app.config.loader import load
    from workspace_app.filestore.blob_gc import BlobGcJob
    from workspace_app.worker.__main__ import build_coordinator

    from .api._client import TestClient

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(f"""
            server:
              run_consumers: false      # a pure producer: the ask is a row, not a pass
            filestore:
              kind: specstar
              gc_interval_sec: 0.05
            sandbox:
              root: {tmp_path / "sandbox"}   # the local sandbox mkdirs its root at boot
            goal:
              offhours:
                window: "22:00-06:00"   # the once-conditional registration
            kb:
              embedder:
                model: ""               # keep boot off the network
        """),
        encoding="utf-8",
    )
    settings = load(config_path=cfg, env={})
    # Tool packages are prebuilt bundles the API refuses to boot without; the
    # explicit opt-out (an empty PACKAGES) is the documented way to run without.
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})

    app = build_app(settings, config_dir=None)
    oracle = app.state.blob_gc_coordinator._spec  # the asker's own registry
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
    # The lifespan adds nothing: a model it added would be one the worker
    # (which never enters one) lacks — the state the first version of this
    # test was green over.
    assert api_models == before_lifespan
    assert {"workspace-file", "-goalstretch", "-scheduleindex", "-triggerwindow"} <= api_models
    assert set(job.payload.registry) == api_models  # the ask names what the API holds

    coordinator = build_coordinator(settings, "blob-gc", config_dir=None)
    worker_models = set(coordinator._spec.resource_managers)  # ty: ignore[unresolved-attribute]
    assert worker_models == api_models, (
        f"asker-runner {sorted(api_models - worker_models)}, runner-asker "
        f"{sorted(worker_models - api_models)}: a pass on the runner would quarantine, then "
        "delete, every blob the models it lacks reference"
    )
    coordinator._check_registry(job.payload.registry)  # ty: ignore[unresolved-attribute]
