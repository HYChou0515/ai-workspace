"""kb_search's permission-disclosure hook: after searching the readable scope, it
probes the read_meta-only (discoverable) collections and, for a competitive match,
accumulates the collection id onto the turn context AND tells the agent a COUNT
only — never the name or content (so a small model can't hallucinate the withheld
answer, yet won't confidently claim nothing exists).
"""

from __future__ import annotations

from agents import RunContextWrapper
from specstar import SpecStar

from workspace_app.agent import AgentToolContext, kb_search_impl
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk

E0 = [1.0] + [0.0] * (EMBED_DIM - 1)  # the query vector (see _QueryVec)


class _QueryVec:
    """Query always maps to E0, so a chunk seeded with E0 is a distance-0 match."""

    dim = EMBED_DIM
    identity = "queryvec"

    def embed_query(self, text: str) -> list[float]:
        return list(E0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        return [list(E0) for _ in texts]


def _coll(spec: SpecStar, name: str) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _seed(spec: SpecStar, cid: str, vec: list[float]) -> None:
    spec.get_resource_manager(DocChunk).create(
        DocChunk(collection_id=cid, seq=0, start=0, end=1, text="x", embedding=list(vec))
    )


def _ctx(spec: SpecStar, *, readable: list[str], discoverable: list[str]):
    return RunContextWrapper(
        AgentToolContext(
            retriever=Retriever(spec, embedder=_QueryVec(), disclosure_floor=0.5),
            collection_ids=readable,
            discoverable_collection_ids=discoverable,
        )
    )


def test_a_withheld_source_with_a_strong_match_is_accumulated_with_a_count_only_note(
    spec: SpecStar,
):
    secret = _coll(spec, "Sales-Secret-2026")
    _seed(spec, secret, E0)  # a distance-0 (competitive) match
    ctx = _ctx(spec, readable=[], discoverable=[secret])

    out = kb_search_impl(ctx, "the query")

    # accumulated for the persist step (→ WithheldSource chip)
    assert ctx.context.withheld_collection_ids == [secret]
    # the agent is told the COUNT, and told NOT to guess — but never the name
    assert "1 knowledge source" in out
    assert "request access" in out
    assert "Sales-Secret-2026" not in out  # the name never leaks in-band


def test_no_discoverable_collections_adds_no_note_and_no_accumulation(spec: SpecStar):
    readable = _coll(spec, "public")
    _seed(spec, readable, E0)
    ctx = _ctx(spec, readable=[readable], discoverable=[])

    out = kb_search_impl(ctx, "the query")

    assert ctx.context.withheld_collection_ids == []
    assert "access" not in out.lower()


def test_a_withheld_source_below_the_floor_is_not_disclosed(spec: SpecStar):
    weak = _coll(spec, "Weakly-Related")
    _seed(spec, weak, [0.0, 1.0] + [0.0] * (EMBED_DIM - 2))  # distance 1.0 > floor 0.5
    ctx = _ctx(spec, readable=[], discoverable=[weak])

    kb_search_impl(ctx, "the query")

    assert ctx.context.withheld_collection_ids == []


def test_the_same_withheld_source_is_not_double_counted_across_searches(spec: SpecStar):
    secret = _coll(spec, "Secret")
    _seed(spec, secret, E0)
    ctx = _ctx(spec, readable=[], discoverable=[secret])

    kb_search_impl(ctx, "first query")
    out2 = kb_search_impl(ctx, "second query")

    assert ctx.context.withheld_collection_ids == [secret]  # accumulated once
    assert "access" not in out2.lower()  # already known → no repeated note
