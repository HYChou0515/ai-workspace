"""ClusterMember (#506 P6) — the reconcile projection table.

Card-generation candidates (proposals + term questions) and the collection's
existing cards are projected into one flat table carrying an ``embedding`` Vector,
so a single native cosine query finds the nearest member — whether that's an
existing card (⑥: already explained → suppress / update) or a prior run's pending
candidate (⑤: cross-run duplicate → same cluster). ContextCard itself stays a
deterministic exact-key glossary with no vector, which is why this table exists.
"""

from __future__ import annotations

import hashlib

from specstar import QB
from specstar.types import Binary

from workspace_app.kb.card_gen import ProposedCard
from workspace_app.kb.context_cards import cards_with_ids_for_collections, derive_norm_keys
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.reconcile import (
    Reconciler,
    _wiki_mentions,
    assign_cluster_key,
    collection_wiki_text,
    grade_candidate,
)
from workspace_app.kb.wiki.store import _rid
from workspace_app.resources import Collection, ContextCard, WikiPage, make_spec
from workspace_app.resources.kb import EMBED_DIM, ClusterMember


def _collection(spec, name: str = "c", *, use_wiki: bool = False) -> str:
    return (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, use_wiki=use_wiki))
        .resource_id
    )


def _wiki_page(spec, cid: str, path: str, text: str) -> None:
    spec.get_resource_manager(WikiPage).create(
        WikiPage(collection_id=cid, path=path, content=Binary(data=text.encode())),
        resource_id=_rid(cid, path),
    )


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


def _members(spec, cid: str) -> list[ClusterMember]:
    rm = spec.get_resource_manager(ClusterMember)
    out = []
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        assert isinstance(r.data, ClusterMember)
        out.append(r.data)
    return out


def _card(spec, cid: str, keys: list[str], *, title: str = "", body: str = "") -> str:
    rm = spec.get_resource_manager(ContextCard)
    return rm.create(
        ContextCard(
            collection_id=cid,
            keys=keys,
            norm_keys=derive_norm_keys(keys),
            title=title,
            body=body,
        )
    ).resource_id


class _TagEmb:
    """A deterministic fake embedder whose vector is decided by the LAST whitespace
    token of the text (its title tag). HashEmbedder is a hash, not a semantic model,
    so it can't stand in for "M4 ≈ Metal 4" nearness; this fake lets a test DECIDE
    which candidates are semantically near (same title tag → identical one-hot →
    cosine 1.0; different tag → orthogonal → cosine 0). It embeds text the same way
    for documents + queries so cluster geometry is symmetric."""

    dim = EMBED_DIM
    identity = "tag"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._v(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._v(text)

    def _v(self, text: str) -> list[float]:
        tag = text.split()[-1] if text.split() else ""
        bucket = int(hashlib.sha256(tag.encode()).hexdigest(), 16) % EMBED_DIM
        v = [0.0] * EMBED_DIM
        v[bucket] = 1.0
        return v


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


# ── Reconciler.reconcile_proposals (orchestration over the pure decisions) ────


def test_reconciler_suppresses_a_proposal_that_duplicates_a_card() -> None:
    """A proposal whose keys don't EXACTLY overlap an existing card (so the #175
    exact classifier left it "new") but whose embedding matches a card is dropped
    from the run's proposals and recorded as a suppressed, auditable member."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, ["alpha"], title="TAGX")  # existing card, title tag TAGX
    existing = cards_with_ids_for_collections(spec, [cid])
    rec = Reconciler(spec, _TagEmb(), cluster_tau=0.5, suppress_tau=0.9, update_tau=0.7)
    dup = ProposedCard(keys=["beta"], title="TAGX", mode="new")  # different key, same tag
    kept = rec.reconcile_proposals(cid, "run1", [dup], existing)
    assert kept == []
    members = _members(spec, cid)
    supp = [m for m in members if m.kind == "proposal" and m.state == "suppressed"]
    assert len(supp) == 1
    assert supp[0].ref_id == dup.id  # ids were assigned so the audit row addresses it


def test_reconciler_keeps_a_new_proposal_and_clusters_it() -> None:
    """A proposal near no card is kept in the run and recorded as an ACTIVE member
    carrying a cluster_key (so a later run's duplicate can GROUP BY it, ⑤)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, ["alpha"], title="TAGX")
    existing = cards_with_ids_for_collections(spec, [cid])
    rec = Reconciler(spec, _TagEmb(), cluster_tau=0.5, suppress_tau=0.9, update_tau=0.7)
    fresh = ProposedCard(keys=["gamma"], title="TAGY", mode="new")  # different tag → far
    kept = rec.reconcile_proposals(cid, "run1", [fresh], existing)
    assert [p.keys for p in kept] == [["gamma"]]
    active = [m for m in _members(spec, cid) if m.kind == "proposal" and m.state == "active"]
    assert len(active) == 1
    assert active[0].cluster_key == "gamma"  # opened its own cluster (norm_key)


def test_reconciler_second_run_duplicate_joins_the_first_runs_cluster() -> None:
    """⑤: a semantically-equal candidate from a LATER run adopts the first run's
    cluster_key, so the inbox can collapse them into one row."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    existing = cards_with_ids_for_collections(spec, [cid])  # no cards
    rec = Reconciler(spec, _TagEmb(), cluster_tau=0.5, suppress_tau=1.01, update_tau=1.01)
    first = ProposedCard(keys=["gamma"], title="TAGZ", mode="new")
    rec.reconcile_proposals(cid, "run1", [first], existing)
    second = ProposedCard(keys=["gamma-syn"], title="TAGZ", mode="new")  # same tag, other key
    rec.reconcile_proposals(cid, "run2", [second], existing)
    active = [m for m in _members(spec, cid) if m.kind == "proposal" and m.state == "active"]
    assert {m.cluster_key for m in active} == {"gamma"}  # both in one cluster
    assert {m.run_id for m in active} == {"run1", "run2"}


def test_reconciler_marks_update_when_partially_near_a_card() -> None:
    """A proposal in the update band gets mode="update" pointing at the card, and is
    kept (a human confirms the edit)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    card_id = _card(spec, cid, ["alpha"], title="TAGX")
    existing = cards_with_ids_for_collections(spec, [cid])
    rec = Reconciler(spec, _TagEmb(), cluster_tau=0.5, suppress_tau=1.01, update_tau=0.6)
    p = ProposedCard(keys=["beta"], title="TAGX", mode="new")  # same tag as the card
    kept = rec.reconcile_proposals(cid, "run1", [p], existing)
    assert len(kept) == 1
    assert kept[0].mode == "update"
    assert kept[0].target_card_id == card_id


# ── wiki-grep safety net (⑥: already documented in the wiki → suppress) ───────


def test_reconciler_keeps_a_proposal_the_wiki_merely_mentions() -> None:
    """A glossary card and a wiki page are NOT substitutes (#537 locked the three
    sources as non-interchangeable), so "the wiki mentions this term" is not a
    reason to withhold the card: the card is the cheap exact-key lookup, the wiki
    is a reader sub-agent. Only an existing CARD may suppress a proposal — which
    is why picking a wiki page as the card-gen source used to yield nothing at
    all, every drafted key being present in the very corpus it was drafted from."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    existing = cards_with_ids_for_collections(spec, [cid])  # no cards
    rec = Reconciler(
        spec,
        _TagEmb(),
        cluster_tau=0.5,
        suppress_tau=1.01,  # never suppress via near-card
        update_tau=1.01,
        wiki_text=lambda _cid: "The term Gamma is fully documented on this page.",
    )
    p = ProposedCard(keys=["Gamma"], title="TAGZ", mode="new")
    kept = rec.reconcile_proposals(cid, "run1", [p], existing)
    assert [x.keys for x in kept] == [["Gamma"]]
    supp = [m for m in _members(spec, cid) if m.kind == "proposal" and m.state == "suppressed"]
    assert supp == []


def test_collection_wiki_text_concatenates_pages_only_when_wiki_is_on() -> None:
    """collection_wiki_text returns the collection's whole wiki as one string when
    use_wiki is set, and "" when it isn't (so a no-wiki collection skips the grep)."""
    spec = make_spec(default_user="u")
    on = _collection(spec, "on", use_wiki=True)
    _wiki_page(spec, on, "/a.md", "alpha content")
    _wiki_page(spec, on, "/b.md", "beta content")
    blob = collection_wiki_text(spec, on)
    assert "alpha content" in blob and "beta content" in blob

    off = _collection(spec, "off", use_wiki=False)
    _wiki_page(spec, off, "/a.md", "gamma content")
    assert collection_wiki_text(spec, off) == ""


# ── term-question suppression (③⑥: already explained → don't re-ask) ──────────


def test_reconciler_suppresses_a_wiki_documented_term_question() -> None:
    """A raised term already explained in the wiki is suppressed — the DocQuestion is
    NOT opened, but an auditable suppressed member records why (③⑥)."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    rec = Reconciler(
        spec,
        _TagEmb(),
        cluster_tau=0.5,
        suppress_tau=1.01,  # never suppress via near-card
        update_tau=1.01,
        wiki_text=lambda _cid: "The term Widget is fully documented here.",
    )
    opened: list[str] = []
    rec.reconcile_term_questions(cid, [("Widget", lambda: opened.append("q1") or "q1")])
    assert opened == []  # the question was never opened
    supp = [m for m in _members(spec, cid) if m.kind == "term_question" and m.state == "suppressed"]
    assert len(supp) == 1
    assert supp[0].reason == "wiki"
    assert supp[0].label == "Widget"


def _ask(spec, cid: str, term: str, wiki: str) -> bool:
    """Run one term through the wiki net; True when the question WAS opened."""
    rec = Reconciler(
        spec,
        _TagEmb(),
        cluster_tau=0.5,
        suppress_tau=1.01,  # never suppress via near-card — isolate the wiki axis
        update_tau=1.01,
        wiki_text=lambda _cid: wiki,
    )
    opened: list[str] = []
    rec.reconcile_term_questions(cid, [(term, lambda: opened.append("q") or "q")])
    return opened == ["q"]


def test_wiki_mentions_bounds_every_script_that_has_word_boundaries() -> None:
    """The boundary rule is about SCRIPT, not about ASCII. Any script that writes
    word boundaries (Latin with diacritics, Greek, full-width digits — not just
    a-z0-9) must demand one; only scriptio-continua scripts (CJK ideographs,
    kana), which write no spaces between words, may match as bare substrings.
    Restricting the rule to ASCII would leave the very bug it fixes alive in
    every other alphabet. The blob arrives already lower-cased."""
    # A bounded script: a longer word is not this term.
    assert not _wiki_mentions("r70 is the seventieth zone.", "R7")
    assert not _wiki_mentions("cafés everywhere", "café")
    assert _wiki_mentions("a café here", "café")
    # Full-width digits are alphanumeric, so they continue an ASCII token.
    assert not _wiki_mentions("r7７ is a code", "R7")
    # Scriptio continua: no boundaries exist to honour.
    assert _wiki_mentions("乾式蝕刻製程的說明。", "蝕刻")
    assert _wiki_mentions("ドライエッチング処理", "エッチング")


def test_wiki_mentions_judges_each_end_of_a_mixed_term_independently() -> None:
    """A term can straddle both worlds ("光罩m4"), so the two ends are decided
    separately. Collapsing the two flags into one — by AND or by OR — must break
    something here, or the claim is untested decoration."""
    # CJK head (no boundary needed) + ASCII tail (boundary needed).
    assert not _wiki_mentions("光罩m40 的說明", "光罩m4")  # AND-collapse would allow this
    assert _wiki_mentions("光罩m4 的說明", "光罩m4")
    # ASCII head (boundary needed) + CJK tail (none needed).
    assert _wiki_mentions("r7 說明abc", "r7 說明")  # OR-collapse would reject this
    assert not _wiki_mentions("xr7 說明", "r7 說明")


def test_wiki_mentions_treats_underscore_as_token_and_hyphen_as_boundary() -> None:
    """Pinned deliberately: identifiers carry underscores (``m4_5`` is its own
    thing, not a mention of ``m4``) while hyphens separate a code from a substep
    (``R7-2`` does mention ``R7``)."""
    assert not _wiki_mentions("m4_5 layer", "m4")
    assert _wiki_mentions("r7-2 is a substep", "r7")


def test_the_wiki_net_matches_on_term_boundaries_not_bare_substrings() -> None:
    """The corpus is mixed Chinese/English and the terminology is largely
    alphanumeric codes (M1-M6, R7), so a bare substring test silently swallows
    questions: "R7" appears inside "R70". Boundaries are decided per CHARACTER
    CLASS, not per language — an ASCII-alphanumeric edge demands a non-alnum
    neighbour, a CJK edge demands nothing (Chinese has no word boundaries), and
    a mixed term gets each rule on its own side. Same standard as the project's
    element-membership rule, where "m4" must not match a card keyed "m40"."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    # ASCII edges: a longer code is NOT this term.
    assert _ask(spec, cid, "R7", "R70 is the seventieth reflow zone.")
    assert _ask(spec, cid, "M4", "The M40 mask layer is documented.")
    # ASCII edges: a real mention still hits, punctuation and CJK both counting
    # as boundaries — including one flush against the end of the corpus.
    assert not _ask(spec, cid, "R7", "R7 的定義如下。")
    assert not _ask(spec, cid, "M4", "(M4) is the fourth metal layer.")
    # A false hit does not end the scan: the real mention follows it.
    assert not _ask(spec, cid, "R7", "R70 is one zone, and so is R7")
    # A CJK term has no boundaries to honour — substring is the correct rule.
    assert not _ask(spec, cid, "蝕刻", "乾式蝕刻製程的說明。")
    # An empty corpus (no wiki / wiki off) can mention nothing.
    assert _ask(spec, cid, "R7", "")


def test_a_blank_term_never_reaches_a_human() -> None:
    """A drafter that emits an empty term has produced nothing to ask about, so no
    question is opened and no member is recorded. Worth pinning because the old
    substring net swallowed it only BY ACCIDENT — `"" in blob` is True — which
    means it leaked a blank question through whenever the collection had no wiki
    at all. Neither outcome should depend on whether a wiki happens to exist."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    for wiki in ("Some wiki content.", ""):
        assert not _ask(spec, cid, "   ", wiki)
    assert _members(spec, cid) == []


def test_a_wiki_mentioned_proposal_in_the_update_band_becomes_an_update() -> None:
    """Both axes live at once. A proposal the wiki mentions AND that sits near an
    existing card is no longer swallowed with reason="wiki" — it surfaces as the
    "update card X" row the card axis says it is. The wiki no longer has a vote
    on proposals, so it cannot pre-empt that verdict."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    card_id = _card(spec, cid, ["beta"], title="TAGX")
    existing = cards_with_ids_for_collections(spec, [cid])
    rec = Reconciler(
        spec,
        _TagEmb(),
        cluster_tau=0.5,
        suppress_tau=1.01,  # unreachable → an exact tag match lands in the update band
        update_tau=0.5,
        wiki_text=lambda _cid: "beta is described at length in the wiki.",
    )
    p = ProposedCard(keys=["beta"], title="TAGX", mode="new")
    kept = rec.reconcile_proposals(cid, "run1", [p], existing)
    assert [x.mode for x in kept] == ["update"]
    assert kept[0].target_card_id == card_id
    assert [m for m in _members(spec, cid) if m.state == "suppressed"] == []


def test_reconciler_suppresses_a_term_question_near_an_existing_card() -> None:
    """A term semantically covered by an existing card is suppressed too — no wiki
    needed (the near-card safety net, mirroring proposals). The card member shares
    the "WID" title tag with the term, so _TagEmb makes them cosine-1.0."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    emb = _TagEmb()
    _member(spec, cid, kind="card", ref_id="c1", cluster_key="wid", vec=emb.embed_query("card WID"))
    rec = Reconciler(spec, emb, cluster_tau=0.5, suppress_tau=0.9, update_tau=0.7)
    opened: list[str] = []
    rec.reconcile_term_questions(cid, [("Gadget WID", lambda: opened.append("q") or "q")])
    assert opened == []
    supp = [m for m in _members(spec, cid) if m.kind == "term_question" and m.state == "suppressed"]
    assert len(supp) == 1
    assert supp[0].reason == "near-card"


def test_reconciler_opens_an_undocumented_term_question() -> None:
    """A term neither in the wiki nor near a card is opened + projected active."""
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    rec = Reconciler(
        spec,
        _TagEmb(),
        cluster_tau=0.5,
        suppress_tau=1.01,
        update_tau=1.01,
        wiki_text=lambda _cid: "an unrelated page about other things",
    )
    opened: list[str] = []

    def _open() -> str:
        opened.append("x")
        return "q7"

    rec.reconcile_term_questions(cid, [("Widget", _open)])
    assert opened == ["x"]  # opened once
    active = [m for m in _members(spec, cid) if m.kind == "term_question" and m.state == "active"]
    assert len(active) == 1
    assert active[0].ref_id == "q7"
