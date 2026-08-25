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
