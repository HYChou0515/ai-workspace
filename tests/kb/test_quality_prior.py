"""Issue #105: the retriever down-weights low-quality docs via a second-phase
additive document prior (IR document-prior literature — Craswell et al. SIGIR'05;
Kraaij/Westerveld SIGIR'02). The prior is *soft* (never a hard filter by default),
*small* (a real relevance gap still wins), *centered* (un-scored = neutral), and
*scoped* to collections that actually have quality scores (un-scored collections
rank exactly as before)."""

from __future__ import annotations

import msgspec

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection, SourceDoc


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


def test_quality_prior_breaks_a_tie_in_favour_of_the_better_doc(spec, chunker, embedder):
    # Two docs with IDENTICAL text ⇒ identical relevance; the only differentiator
    # is quality, so the higher-quality doc must rank first.
    cid = _collection(spec)
    _ingest(spec, chunker, embedder, cid, "good.md", "alpha beta gamma")
    _ingest(spec, chunker, embedder, cid, "bad.md", "alpha beta gamma")
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


def test_hard_floor_excludes_below_threshold_when_configured(spec, chunker, embedder):
    cid = _collection(spec)
    _ingest(spec, chunker, embedder, cid, "ok.md", "alpha beta gamma")
    _ingest(spec, chunker, embedder, cid, "garbage.md", "alpha beta gamma")
    _set_quality(spec, cid, "ok.md", 60)
    _set_quality(spec, cid, "garbage.md", 5)

    passages = Retriever(spec, embedder=embedder, quality_floor=20).search(
        "alpha beta gamma", [cid]
    )
    ids = [p.document_id for p in passages]
    assert encode_doc_id(cid, "garbage.md") not in ids
    assert encode_doc_id(cid, "ok.md") in ids
