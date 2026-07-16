"""The retrieval-side disclosure probe (`Retriever.probe_withheld`).

D9: to detect that a collection the user can see-exist-but-not-read holds a
relevant answer, we run a SEPARATE scores-only dense pass — the main `search()`
path is untouched, and the withheld chunks' text/vectors never leave the probe.
The probe returns ONLY collection ids that clear the disclosure threshold:

  disclose a withheld collection iff its best (nearest) chunk's cosine distance
  ≤ min(the weakest readable top-k distance, the absolute relevance floor)

so it must be at least as relevant as something we DID show (competitive) AND
absolutely relevant (no noise). With nothing readable, the floor alone decides —
the motivating case where the only answer is in a collection you can't read.

Distances are controlled deterministically via a fixed query vector + directly
seeded chunk vectors, so the threshold logic is exercised exactly, independent of
any embedder's semantics.
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk

Q = "the query"


def _e(i: int) -> list[float]:
    """The i-th standard basis vector in EMBED_DIM space (a clean unit vector so
    cosine distances between distinct axes are exactly 1.0)."""
    v = [0.0] * EMBED_DIM
    v[i] = 1.0
    return v


E0 = _e(0)  # the query vector (see _QueryVec)
OPP = [-1.0] + [0.0] * (EMBED_DIM - 1)  # cosine distance 2.0 from E0 (weakest possible)


class _QueryVec:
    """A minimal Embedder whose query always maps to E0, so a chunk seeded with a
    known vector has a known cosine distance to the query. `embed_documents` is
    unused by the probe but present to satisfy the Protocol."""

    dim = EMBED_DIM
    identity = "queryvec"

    def embed_query(self, text: str) -> list[float]:
        return list(E0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        return [list(E0) for _ in texts]


def _coll(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id


def _seed(spec: SpecStar, cid: str, vec: list[float]) -> None:
    """Seed ONE chunk with an explicit embedding into `cid`. The probe reads only
    collection_id + embedding, so no SourceDoc is needed."""
    spec.get_resource_manager(DocChunk).create(
        DocChunk(collection_id=cid, seq=0, start=0, end=1, text="x", embedding=list(vec))
    )


def _retriever(spec: SpecStar, floor: float) -> Retriever:
    return Retriever(spec, embedder=_QueryVec(), disclosure_floor=floor)


def test_no_withheld_collections_returns_empty(spec: SpecStar):
    r = _coll(spec)
    _seed(spec, r, _e(1))
    assert _retriever(spec, 2.0).probe_withheld(Q, [r], []) == []


def test_withheld_strong_match_beats_a_weak_readable_and_is_disclosed(spec: SpecStar):
    # readable's best is distance 1.0; withheld has a distance-0 hit → competitive.
    r, w = _coll(spec), _coll(spec)
    _seed(spec, r, _e(1))  # dist 1.0
    _seed(spec, w, E0)  # dist 0.0
    assert _retriever(spec, 2.0).probe_withheld(Q, [r], [w]) == [w]


def test_withheld_weaker_than_readable_is_not_disclosed(spec: SpecStar):
    # readable already surfaces a distance-0 hit; the withheld (dist 1.0) is less
    # relevant than what we showed → nothing to disclose.
    r, w = _coll(spec), _coll(spec)
    _seed(spec, r, E0)  # dist 0.0
    _seed(spec, w, _e(1))  # dist 1.0
    assert _retriever(spec, 2.0).probe_withheld(Q, [r], [w]) == []


def test_no_readable_uses_the_absolute_floor_and_discloses_a_strong_match(spec: SpecStar):
    # The motivating case: the ONLY answer lives where you can't read content.
    w = _coll(spec)
    _seed(spec, w, E0)  # dist 0.0 ≤ floor 0.5
    assert _retriever(spec, 0.5).probe_withheld(Q, [], [w]) == [w]


def test_no_readable_floor_blocks_a_weak_match(spec: SpecStar):
    w = _coll(spec)
    _seed(spec, w, _e(1))  # dist 1.0 > floor 0.5
    assert _retriever(spec, 0.5).probe_withheld(Q, [], [w]) == []


def test_absolute_floor_caps_a_loose_readable_cutoff(spec: SpecStar):
    # readable is near-worthless (dist 2.0), so a bare "beat the weakest shown"
    # rule would disclose a mediocre withheld hit (dist 1.0). The floor caps it:
    # min(2.0, 0.5) = 0.5, and 1.0 > 0.5 → not disclosed (noise guard).
    r, w = _coll(spec), _coll(spec)
    _seed(spec, r, OPP)  # dist 2.0
    _seed(spec, w, _e(1))  # dist 1.0
    assert _retriever(spec, 0.5).probe_withheld(Q, [r], [w]) == []


def test_a_withheld_collection_is_disclosed_by_its_best_chunk(spec: SpecStar):
    # Two chunks in the withheld collection: one weak, one strong. The MIN distance
    # decides, and the collection is disclosed exactly once.
    w = _coll(spec)
    _seed(spec, w, _e(1))  # dist 1.0 (weak)
    _seed(spec, w, E0)  # dist 0.0 (strong)
    assert _retriever(spec, 0.5).probe_withheld(Q, [], [w]) == [w]


def test_multiple_withheld_are_returned_in_input_order(spec: SpecStar):
    w1, w2, w3 = _coll(spec), _coll(spec), _coll(spec)
    _seed(spec, w1, E0)  # dist 0.0 — disclosed
    _seed(spec, w2, _e(1))  # dist 1.0 — blocked by floor 0.5
    _seed(spec, w3, E0)  # dist 0.0 — disclosed
    # input order w3-before-w1 is preserved in the output
    assert _retriever(spec, 0.5).probe_withheld(Q, [], [w3, w2, w1]) == [w3, w1]
