"""`build_coordinators` (#312) — the shared composition root for the background
job coordinators, used by BOTH `create_app` (API, pure producer) and the
standalone worker entrypoint. It must return the SAME wired set the API used to
build inline, so a doc enqueued through the bundle's index coordinator is
indexed off the request path."""

from __future__ import annotations

from specstar.types import Binary

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.api import ScriptedAgentRunner
from workspace_app.coordinators import CoordinatorBundle, build_coordinators
from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
from workspace_app.kb.index_coordinator import IndexCoordinator
from workspace_app.kb.wiki.coordinator import WikiMaintenanceCoordinator
from workspace_app.resources import Collection, SourceDoc, make_spec


class _FakeIngestor:
    def __init__(self) -> None:
        self.indexed: list[str] = []

    def index(
        self, doc_id: str, *, source_doc_rm: object | None = None, reraise: bool = False
    ) -> None:
        self.indexed.append(doc_id)


def _collection(spec) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id


def _doc(spec, cid: str) -> str:
    return (
        spec.get_resource_manager(SourceDoc)
        .create(
            SourceDoc(collection_id=cid, path="a.md", content=Binary(data=b"x"), status="indexing")
        )
        .resource_id
    )


def _build(spec, ingestor, **overrides) -> CoordinatorBundle:
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


async def test_build_coordinators_returns_a_bundle_that_indexes_off_the_request_path():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    doc_id = _doc(spec, cid)
    ing = _FakeIngestor()

    bundle = _build(spec, ing)

    # The bundle exposes the real coordinator set the API used to build inline.
    assert isinstance(bundle.wiki, WikiMaintenanceCoordinator)
    assert isinstance(bundle.index, IndexCoordinator)
    assert isinstance(bundle.card_gen, CardGenCoordinator)
    # Optional coordinators stay None when their LLM seam is unwired.
    assert bundle.quality is None  # no quality_judge_llm
    assert bundle.sanity is None  # no sanity_llm_factory

    bundle.index.enqueue(doc_id, cid)  # producer returns immediately
    await bundle.index.aclose()  # drain the background consumer

    assert ing.indexed == [doc_id]  # the index coordinator ran off the request path


def test_quality_and_sanity_coordinators_are_built_when_their_llm_is_wired():
    spec = make_spec(default_user="u")

    class _Llm:  # minimal ILlm stand-in; never called in construction
        pass

    bundle = _build(
        spec,
        _FakeIngestor(),
        quality_judge_llm=_Llm(),
        sanity_llm_factory=lambda model, level: _Llm(),
        sanity_judge_llm=_Llm(),
    )

    assert bundle.quality is not None  # wired because a judge llm was passed
    assert bundle.sanity is not None  # wired because a sanity factory was passed


def test_a_wiki_model_wires_the_code_wiki_builder():
    # #281: when a wiki model/endpoint is configured, the coordinator gets a
    # deterministic code-wiki summariser (built from the same endpoint), so a
    # code collection can rebuild its wiki. No model ⇒ no builder.
    # a fresh spec per build — registering the job model twice on one spec raises
    assert _build(make_spec(default_user="u"), _FakeIngestor()).wiki._code_builder is None
    wired = _build(make_spec(default_user="u"), _FakeIngestor(), wiki_model="ollama/qwen3:8b")
    assert wired.wiki._code_builder is not None


def test_an_embedder_wires_the_cardgen_reconciler():
    # #506 P6: the card-gen reconcile needs an embedder to compare candidates +
    # cards in one vector space. No embedder ⇒ the coordinator stays exact-only.
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.resources.kb import EMBED_DIM

    assert _build(make_spec(default_user="u"), _FakeIngestor()).card_gen._reconciler is None
    wired = _build(
        make_spec(default_user="u"), _FakeIngestor(), embedder=HashEmbedder(dim=EMBED_DIM)
    )
    assert wired.card_gen._reconciler is not None
