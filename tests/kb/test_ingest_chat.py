"""End-to-end: Ingestor.ingest_chat takes a RCA Conversation + investigation
title, runs the chat pipeline (InsightExtractor → DispatchSplitter →
EmbedderAdapter), and writes each insight as a SourceDoc + DocChunk into a
dedicated 'Investigations Knowledge' collection. See plan §3.
"""

from __future__ import annotations

from collections.abc import Iterator

from specstar import QB, SpecStar

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_chat_pipeline
from workspace_app.kb.llm import ILlm
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk, SourceDoc

_INSIGHTS_COLLECTION = "Investigations Knowledge"


class _FakeLlm(ILlm):
    def __init__(self, response: str) -> None:
        self._response = response

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._response, False)


def _ensure_insights_collection(spec: SpecStar) -> str:
    rm = spec.get_resource_manager(Collection)
    return rm.create(Collection(name=_INSIGHTS_COLLECTION)).resource_id


def test_ingest_chat_writes_insights_as_sourcedocs(spec: SpecStar, embedder: HashEmbedder):
    """A conversation + LLM-suggested insights → one SourceDoc per insight
    in the insights collection, each with embedded DocChunks."""
    cid = _ensure_insights_collection(spec)
    llm = _FakeLlm(
        '{"insights": ['
        '  {"kind": "root_cause", "title": "Zone-3 drift",'
        '   "markdown": "# Root cause: Zone-3 drift\\n\\nThermocouple calibration miss."},'
        '  {"kind": "procedure", "title": "Recal procedure",'
        '   "markdown": "# Procedure\\n\\nSteps 1, 2, 3 to recalibrate."}'
        "]}"
    )
    pipeline = build_chat_pipeline(llm=llm, embedder=embedder)
    ingestor = Ingestor(spec, chat_pipeline=pipeline, embedder=embedder)

    ids = ingestor.ingest_chat(
        collection_id=cid,
        user="system",
        investigation_id="inv-123",
        investigation_title="MX-7 voids",
        messages=[
            {"role": "user", "content": "AOI flagged voids on lot 25-W14"},
            {"role": "assistant", "content": "I'll check the zone temps."},
        ],
    )
    # Two insights → two SourceDocs, each at a deterministic path.
    assert len(ids) == 2
    docs = [spec.get_resource_manager(SourceDoc).get(i).data for i in ids]
    assert {d.path for d in docs} == {"inv-123/insight-0.md", "inv-123/insight-1.md"}
    # All under the insights collection.
    assert all(d.collection_id == cid for d in docs)
    # Each insight produced at least one embedded chunk.
    chrm = spec.get_resource_manager(DocChunk)
    for doc_id in ids:
        chunks = chrm.list_resources((QB["source_doc_id"] == doc_id).build())
        chunks = [r.data for r in chunks]
        assert len(chunks) >= 1
        assert all(len(c.embedding) == EMBED_DIM for c in chunks)


def test_ingest_chat_with_no_insights_returns_empty(spec: SpecStar, embedder: HashEmbedder):
    """An inconclusive chat (LLM returns `[]`) writes nothing — no SourceDoc
    churn, no noise in the KB."""
    cid = _ensure_insights_collection(spec)
    llm = _FakeLlm('{"insights": []}')
    pipeline = build_chat_pipeline(llm=llm, embedder=embedder)
    ingestor = Ingestor(spec, chat_pipeline=pipeline, embedder=embedder)

    ids = ingestor.ingest_chat(
        collection_id=cid,
        user="system",
        investigation_id="inv-empty",
        investigation_title="inconclusive",
        messages=[{"role": "user", "content": "no findings"}],
    )
    assert ids == []


def test_re_ingest_chat_overwrites_in_place(spec: SpecStar, embedder: HashEmbedder):
    """Promoting the same chat twice (e.g. user clicks promote, then chat
    keeps going, user re-promotes) reuses the deterministic doc_ids — the
    insights are overwritten, not duplicated."""
    cid = _ensure_insights_collection(spec)
    llm1 = _FakeLlm(
        '{"insights": [{"kind": "root_cause", "title": "v1", "markdown": "# v1\\n\\nold"}]}'
    )
    pipeline1 = build_chat_pipeline(llm=llm1, embedder=embedder)
    ingestor1 = Ingestor(spec, chat_pipeline=pipeline1, embedder=embedder)
    ids_first = ingestor1.ingest_chat(
        collection_id=cid,
        user="system",
        investigation_id="inv-x",
        investigation_title="t",
        messages=[{"role": "user", "content": "hi"}],
    )

    llm2 = _FakeLlm(
        '{"insights": [{"kind": "root_cause", "title": "v2", "markdown": "# v2\\n\\nnew updated"}]}'
    )
    pipeline2 = build_chat_pipeline(llm=llm2, embedder=embedder)
    ingestor2 = Ingestor(spec, chat_pipeline=pipeline2, embedder=embedder)
    ids_second = ingestor2.ingest_chat(
        collection_id=cid,
        user="system",
        investigation_id="inv-x",
        investigation_title="t",
        messages=[{"role": "user", "content": "hi"}],
    )

    # Same id reused: deterministic from (investigation_id, insight_seq).
    assert ids_first == ids_second
    doc = spec.get_resource_manager(SourceDoc).get(ids_second[0]).data
    raw = spec.get_resource_manager(SourceDoc).restore_binary(doc).content.data
    assert b"new updated" in raw
    assert b"old" not in raw
