"""Issue #105: the retriever down-weights low-quality docs via a second-phase
additive document prior (IR document-prior literature — Craswell et al. SIGIR'05;
Kraaij/Westerveld SIGIR'02). The prior is *soft* (never a hard filter by default),
*small* (a real relevance gap still wins), *centered* (un-scored = neutral), and
*scoped* to collections that actually have quality scores (un-scored collections
rank exactly as before)."""

from __future__ import annotations

import msgspec
from specstar import QB

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection, DocChunk, SourceDoc


def _collection(spec) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _ingest(spec, chunker, embedder, cid, name, text):
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename=name, data=text.encode()
    )


def _set_quality(spec, cid, name, score):
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, name)
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    rm.update(doc_id, msgspec.structs.replace(doc, quality_score=score))


def _rank(passages, cid, name):
    ids = [p.document_id for p in passages]
    return ids.index(encode_doc_id(cid, name))


def _force_chunk_embedding(spec, cid, name, vec):
    """Overwrite a doc's chunk embeddings with `vec` — used to force a perfect
    dense-relevance tie between two DISTINCT docs (see the tie-break test)."""
    rm = spec.get_resource_manager(DocChunk)
    doc_id = encode_doc_id(cid, name)
    for r in rm.list_resources((QB["source_doc_id"] == doc_id).build()):
        rm.update(r.info.resource_id, msgspec.structs.replace(r.data, embedding=list(vec)))


def test_quality_prior_breaks_a_tie_in_favour_of_the_better_doc(spec, chunker, embedder):
    # #104 dedups byte-identical content, so a relevance tie can't come from two
    # identical docs. Build the tie from two DISTINCT docs instead: the same token
    # multiset in a different order (→ identical BM25, different bytes so both are
    # kept) with their chunk embeddings forced equal (→ identical dense score).
    # The only remaining differentiator is quality, so the better doc ranks first.
    cid = _collection(spec)
    _ingest(spec, chunker, embedder, cid, "good.md", "alpha beta gamma")
    _ingest(spec, chunker, embedder, cid, "bad.md", "gamma beta alpha")
    tie_vec = embedder.embed_documents(["alpha beta gamma"])[0]
    _force_chunk_embedding(spec, cid, "good.md", tie_vec)
    _force_chunk_embedding(spec, cid, "bad.md", tie_vec)
    _set_quality(spec, cid, "good.md", 90)
    _set_quality(spec, cid, "bad.md", 10)

    passages = Retriever(spec, embedder=embedder).search("alpha beta gamma", [cid])
    assert _rank(passages, cid, "good.md") < _rank(passages, cid, "bad.md")


def test_strong_relevance_beats_a_quality_gap(spec, chunker, embedder):
    # A strongly-relevant but LOW-quality doc must still beat a weakly-relevant
    # HIGH-quality doc — the prior is small; relevance dominates (the failure mode
    # the literature warns about is an over-strong prior nuking recall).
    cid = _collection(spec)
    _ingest(spec, chunker, embedder, cid, "strong.md", "alpha beta gamma alpha beta gamma")
    _ingest(spec, chunker, embedder, cid, "weak.md", "padding words only here now then")
    _set_quality(spec, cid, "strong.md", 5)
    _set_quality(spec, cid, "weak.md", 95)

    passages = Retriever(spec, embedder=embedder).search("alpha beta gamma", [cid])
    assert passages[0].document_id == encode_doc_id(cid, "strong.md")


def test_low_quality_doc_is_not_hard_excluded_by_default(spec, chunker, embedder):
    cid = _collection(spec)
    _ingest(spec, chunker, embedder, cid, "only.md", "alpha beta gamma")
    _set_quality(spec, cid, "only.md", 0)

    passages = Retriever(spec, embedder=embedder).search("alpha beta gamma", [cid])
    assert any(p.document_id == encode_doc_id(cid, "only.md") for p in passages)


def test_quality_floor_uses_the_resolved_canonical_docs_score(spec, chunker, embedder):
    # #104 P1: the quality prior scores a chunk by its RESOLVED (content→canonical)
    # doc, not by the chunk's own source_doc_id — quality follows content, exactly
    # like citation resolution. A chunk of low-quality content must be floored out
    # even if its source_doc_id happens to name a high-quality doc.
    cid = _collection(spec)
    _ingest(
        spec, chunker, embedder, cid, "cheap.md", "alpha beta gamma"
    )  # matches query, canonical
    _ingest(spec, chunker, embedder, cid, "posh.md", "unrelated padding tokens only")  # distinct
    _set_quality(spec, cid, "cheap.md", 5)
    _set_quality(spec, cid, "posh.md", 90)
    cheap = encode_doc_id(cid, "cheap.md")
    posh = encode_doc_id(cid, "posh.md")
    # Point cheap's content chunk at the high-quality sibling; the floor must still
    # drop it, scoring by the content's canonical doc (cheap=5), not posh=90.
    chrm = spec.get_resource_manager(DocChunk)
    for r in chrm.list_resources((QB["source_doc_id"] == cheap).build()):
        rm_chunk = r.data
        assert isinstance(rm_chunk, DocChunk)
        chrm.update(r.info.resource_id, msgspec.structs.replace(rm_chunk, source_doc_id=posh))

    passages = Retriever(spec, embedder=embedder, quality_floor=20).search(
        "alpha beta gamma", [cid]
    )

    ids = [p.document_id for p in passages]
    assert cheap not in ids  # floored via canonical (cheap=5), not source_doc_id (posh=90)


def test_hard_floor_excludes_below_threshold_when_configured(spec, chunker, embedder):
    cid = _collection(spec)
    # Distinct bytes (reordered tokens) so #104 keeps both as separate, retrievable
    # docs — the hard floor, not dedup, is what must exclude the low-quality one.
    _ingest(spec, chunker, embedder, cid, "ok.md", "alpha beta gamma")
    _ingest(spec, chunker, embedder, cid, "garbage.md", "gamma beta alpha")
    _set_quality(spec, cid, "ok.md", 60)
    _set_quality(spec, cid, "garbage.md", 5)

    passages = Retriever(spec, embedder=embedder, quality_floor=20).search(
        "alpha beta gamma", [cid]
    )
    ids = [p.document_id for p in passages]
    assert encode_doc_id(cid, "garbage.md") not in ids
    assert encode_doc_id(cid, "ok.md") in ids
