"""ClusterMember (#506 P6) — the reconcile projection table.

Card-generation candidates (proposals + term questions) and the collection's
existing cards are projected into one flat table carrying an ``embedding`` Vector,
so a single native cosine query finds the nearest member — whether that's an
existing card (⑥: already explained → suppress / update) or a prior run's pending
candidate (⑤: cross-run duplicate → same cluster). ContextCard itself stays a
deterministic exact-key glossary with no vector, which is why this table exists.
"""

from __future__ import annotations

from specstar import QB

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.reconcile import assign_cluster_key, grade_candidate
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember


def _collection(spec, name: str = "c") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _member(spec, cid: str, *, kind="proposal", ref_id="", norm_key="", cluster_key="", vec=None):
    rm = spec.get_resource_manager(ClusterMember)
    return rm.create(
        ClusterMember(
            collection_id=cid,
            kind=kind,
            ref_id=ref_id or norm_key,
            norm_key=norm_key,
            cluster_key=cluster_key or norm_key,
            embedding=vec,
        )
    ).resource_id


def test_native_cosine_finds_the_nearest_member() -> None:
    """Tracer: ClusterMember is a registered resource with a cosine Vector, and a
    native ``QB["embedding"].cosine(vec).asc()`` query returns the nearest member
    first — the retrieval primitive the reconcile step is built on."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    rm = spec.get_resource_manager(ClusterMember)
    rm.create(
        ClusterMember(
            collection_id=cid,
            kind="card",
            ref_id="a",
            norm_key="alpha",
            cluster_key="alpha",
            embedding=emb.embed_query("alpha"),
        )
    )
    rm.create(
        ClusterMember(
            collection_id=cid,
            kind="card",
            ref_id="b",
            norm_key="beta",
            cluster_key="beta",
            embedding=emb.embed_query("beta"),
        )
    )
    probe = emb.embed_query("alpha")  # identical text → identical vector → distance 0
    query = (
        (QB["collection_id"] == cid)
        # specstar's order_by type union omits VectorDistanceSort (works at runtime)
        .order_by(QB["embedding"].cosine(probe).asc())  # ty: ignore[invalid-argument-type]
        .limit(1)
        .build()
    )
    hits = list(rm.list_resources(query))
    assert hits, "expected at least one member"
    nearest = hits[0].data
    assert isinstance(nearest, ClusterMember)
    assert nearest.ref_id == "a"


def test_assign_opens_a_new_cluster_when_nothing_is_near() -> None:
    """Tracer: on an empty (or all-far) collection, a candidate opens its own
    cluster keyed by its norm_key."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    key = assign_cluster_key(
        spec,
        collection_id=cid,
        norm_key="zzz",
        embedding=emb.embed_query("zzz"),
        tau=0.9,
    )
    assert key == "zzz"


def test_assign_joins_by_exact_norm_key_regardless_of_distance() -> None:
    """An exact norm_key match is the deterministic identity: the candidate joins
    that member's cluster even if its embedding is nothing like it (race-free burst
    dedup of the same surface form)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    _member(spec, cid, norm_key="m4", cluster_key="grp-m4", vec=emb.embed_query("totally other"))
    key = assign_cluster_key(
        spec,
        collection_id=cid,
        norm_key="m4",
        embedding=emb.embed_query("m4 metal capping"),
        tau=0.99,
    )
    assert key == "grp-m4"


def test_assign_adopts_the_nearest_cluster_above_threshold() -> None:
    """A different surface form for the same concept (no exact norm_key match) joins
    the nearest member's cluster when cosine similarity clears tau."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    _member(spec, cid, norm_key="alpha", cluster_key="grp-alpha", vec=emb.embed_query("alpha"))
    key = assign_cluster_key(
        spec,
        collection_id=cid,
        norm_key="alpha-synonym",  # different key → no exact hit
        embedding=emb.embed_query("alpha"),  # identical vector → similarity 1.0
        tau=0.5,
    )
    assert key == "grp-alpha"


def test_assign_opens_new_cluster_when_nearest_is_below_threshold() -> None:
    """Nearest member exists but is too far → the candidate opens its own cluster
    rather than being force-merged into an unrelated concept."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    _member(spec, cid, norm_key="alpha", cluster_key="grp-alpha", vec=emb.embed_query("alpha"))
    key = assign_cluster_key(
        spec,
        collection_id=cid,
        norm_key="beta",
        embedding=emb.embed_query("beta"),
        tau=0.999,  # unreachable for two distinct hashes → no adopt
    )
    assert key == "beta"


# ── grading (⑥: decide suppress / update / new against existing cards) ────────


def test_grade_suppresses_a_candidate_near_an_existing_card() -> None:
    """Tracer: a candidate whose embedding is (near-)identical to an existing card
    member is already explained → suppressed (auto-dropped, but auditable)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    _member(spec, cid, kind="card", ref_id="card1", norm_key="alpha", vec=emb.embed_query("alpha"))
    g = grade_candidate(
        spec,
        collection_id=cid,
        embedding=emb.embed_query("alpha"),
        tau_high=0.9,
        tau_update=0.75,
    )
    assert g.action == "suppress"
    assert g.target_card_id == "card1"
    assert g.reason == "near-card"


def test_grade_suppresses_on_a_wiki_hit_without_touching_cards() -> None:
    """A wiki grep hit is a deterministic "already explained" signal — suppress even
    when no card is near."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    g = grade_candidate(
        spec,
        collection_id=cid,
        embedding=emb.embed_query("anything"),
        tau_high=0.9,
        tau_update=0.75,
        wiki_hit=True,
    )
    assert g.action == "suppress"
    assert g.reason == "wiki"


def test_grade_updates_when_partially_near_a_card() -> None:
    """A candidate that is related to but not a duplicate of a card (similarity in
    the update band) proposes an UPDATE to that card, for a human to confirm."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    _member(spec, cid, kind="card", ref_id="card1", norm_key="alpha", vec=emb.embed_query("alpha"))
    g = grade_candidate(
        spec,
        collection_id=cid,
        embedding=emb.embed_query("alpha"),
        tau_high=1.01,  # unreachable → never suppress
        tau_update=0.5,  # identical vector clears the update band
    )
    assert g.action == "update"
    assert g.target_card_id == "card1"


def test_grade_is_new_when_no_card_is_near() -> None:
    """No existing card is close enough → a genuinely new concept."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = HashEmbedder(dim=EMBED_DIM)
    _member(spec, cid, kind="card", ref_id="card1", norm_key="alpha", vec=emb.embed_query("alpha"))
    g = grade_candidate(
        spec,
        collection_id=cid,
        embedding=emb.embed_query("beta"),
        tau_high=0.9,
        tau_update=0.999,  # unreachable for distinct hashes
    )
    assert g.action == "new"
    assert g.target_card_id is None
