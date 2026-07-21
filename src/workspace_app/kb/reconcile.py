"""#506 P6 reconcile — assign a ``cluster_key`` to a card-generation candidate.

A candidate joins an existing cluster **deterministically** when it shares an
exact ``norm_key`` with a prior member (no embedding needed — an upload burst of
the same surface form all lands in one cluster, race-free); else by **cosine
nearest-neighbour** when a member is within the similarity threshold ``tau``;
else it opens a brand-new cluster keyed by its own ``norm_key``. The assigned
``cluster_key`` is what the review inbox groups by, so semantically-equal
candidates from different runs collapse to one row (⑤).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from specstar import QB, SpecStar
from specstar.util.vector_distance import cosine_distance

if TYPE_CHECKING:
    from specstar.types import IResourceManager

from ..resources.kb import ClusterMember, Collection, ContextCard, WikiPage
from .card_gen import ProposedCard, ensure_proposal_ids
from .card_proposal import CardProposalStore
from .context_cards import derive_norm_keys, norm
from .doc_questions import questions_by_status
from .embedder import Embedder


def assign_cluster_key(
    spec: SpecStar,
    *,
    collection_id: str,
    norm_key: str,
    embedding: list[float],
    tau: float,
) -> str:
    """Return the ``cluster_key`` a new candidate should join.

    ``tau`` is a cosine SIMILARITY threshold in ``[0, 1]``: the nearest member is
    adopted only when ``similarity >= tau`` (``similarity = 1 - cosine_distance``).
    Exact ``norm_key`` overlap short-circuits the vector query and wins regardless
    of ``tau`` — it is the deterministic identity."""
    rm = spec.get_resource_manager(ClusterMember)
    # Deterministic exact-key fast path — same surface form ⇒ same concept.
    if norm_key:
        exact = ((QB["collection_id"] == collection_id) & (QB["norm_key"] == norm_key)).build()
        for r in rm.list_resources(exact):
            m = r.data
            assert isinstance(m, ClusterMember)
            return m.cluster_key or norm_key
    # Semantic nearest-neighbour — a different surface form for the same concept.
    near = (
        (QB["collection_id"] == collection_id)
        # specstar's order_by type union omits VectorDistanceSort (works at runtime)
        .order_by(QB["embedding"].cosine(embedding).asc())  # ty: ignore[invalid-argument-type]
        .limit(1)
        .build()
    )
    for r in rm.list_resources(near):
        m = r.data
        assert isinstance(m, ClusterMember)
        if m.embedding is not None and 1.0 - cosine_distance(m.embedding, embedding) >= tau:
            return m.cluster_key or m.norm_key
    return norm_key


@dataclass(frozen=True)
class Grade:
    """The reconcile verdict for one candidate against what the collection already
    explains. ``action`` is ``suppress`` (already explained → auto-drop, kept only as
    an auditable ClusterMember), ``update`` (partially covered → suggest editing
    ``target_card_id``), or ``new`` (a genuinely new concept). ``reason`` records WHY
    a suppress fired, for the audit view: ``near-card`` for either kind of candidate,
    ``wiki`` only for a term question — ``wiki_hit`` is never set for a card proposal
    (#537), see :meth:`Reconciler.reconcile_proposals`."""

    action: str  # "suppress" | "update" | "new"
    target_card_id: str | None = None
    reason: str = ""  # "wiki" | "near-card" | ""


def _scriptio_continua(ch: str) -> bool:
    """Is ``ch`` from a script written WITHOUT spaces between words? Those scripts
    have no word boundary to demand, so requiring one would match almost nothing.
    CJK ideographs and Japanese kana are the ones this corpus contains; Korean
    and Thai are deliberately absent — Korean writes spaces, and no Thai has ever
    appeared here, so claiming to handle it would be untested fiction."""
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # hiragana + katakana
        or 0x3400 <= o <= 0x4DBF  # CJK unified ext A
        or 0x4E00 <= o <= 0x9FFF  # CJK unified
        or 0xF900 <= o <= 0xFAFF  # CJK compatibility ideographs
        or 0x20000 <= o <= 0x2FA1F  # CJK unified ext B-F
    )


def _continues_a_token(ch: str) -> bool:
    """Would ``ch`` next to a term's edge make it a LONGER word rather than a
    mention of the term? True for any alphanumeric in a space-writing script, plus
    the underscore (``m4_5`` is its own identifier, not a mention of ``m4``). A
    hyphen is NOT one: ``R7-2`` names a substep OF ``R7``."""
    return ch == "_" or (ch.isalnum() and not _scriptio_continua(ch))


def _wiki_mentions(wiki_blob: str, term: str) -> bool:
    """Does ``wiki_blob`` — which the caller has ALREADY lower-cased — mention
    ``term``?

    A bare substring test is wrong for this corpus. The knowledge base is mixed
    Chinese and English, and much of the terminology is short alphanumeric codes
    (``M1``-``M6``, ``R7``), so ``"R7" in blob`` fires on ``R70`` and silently
    swallows a legitimate question — the same mistake the project already rejects
    for indexed-list membership, where ``"m4"`` must not match ``"m40"``.

    So an edge in a space-writing script demands a neighbour that doesn't continue
    the token, and an edge in a scriptio-continua script demands nothing. The rule
    is about SCRIPT, not about ASCII: restricting it to a-z0-9 would leave the
    same bug alive in every other alphabet (``café`` inside ``cafés``). Each end is
    judged on its own, because one term can straddle both worlds (``光罩m4``)."""
    needle = term.strip().lower()
    if not wiki_blob or not needle:
        return False
    head_bound = _continues_a_token(needle[0])
    tail_bound = _continues_a_token(needle[-1])
    start = wiki_blob.find(needle)
    while start != -1:
        end = start + len(needle)
        before_ok = not head_bound or start == 0 or not _continues_a_token(wiki_blob[start - 1])
        after_ok = not tail_bound or end == len(wiki_blob) or not _continues_a_token(wiki_blob[end])
        if before_ok and after_ok:
            return True
        start = wiki_blob.find(needle, start + 1)
    return False


def grade_candidate(
    spec: SpecStar,
    *,
    collection_id: str,
    embedding: list[float],
    tau_high: float,
    tau_update: float,
    wiki_hit: bool = False,
) -> Grade:
    """Decide a candidate's fate against what the collection ALREADY explains.

    A wiki grep hit is a deterministic "already covered" signal → ``suppress``.
    Otherwise the nearest existing CARD member decides by cosine similarity:
    ``>= tau_high`` → ``suppress`` (a semantic duplicate), ``>= tau_update`` →
    ``update`` that card (related but adds something), else ``new``. This is the
    semantic layer over the exact-key ``classify_against_existing`` (#175)."""
    if wiki_hit:
        return Grade("suppress", reason="wiki")
    rm = spec.get_resource_manager(ClusterMember)
    near_card = (
        ((QB["collection_id"] == collection_id) & (QB["kind"] == "card"))
        # specstar's order_by type union omits VectorDistanceSort (works at runtime)
        .order_by(QB["embedding"].cosine(embedding).asc())  # ty: ignore[invalid-argument-type]
        .limit(1)
        .build()
    )
    for r in rm.list_resources(near_card):
        m = r.data
        assert isinstance(m, ClusterMember)
        if m.embedding is None:
            break
        sim = 1.0 - cosine_distance(m.embedding, embedding)
        if sim >= tau_high:
            return Grade("suppress", target_card_id=m.ref_id, reason="near-card")
        if sim >= tau_update:
            return Grade("update", target_card_id=m.ref_id, reason="near-card")
        break
    return Grade("new")


def _card_text(norm_key: str, title: str) -> str:
    """The short string embedded for both cards + candidates (#506 §embed content):
    the normalised key plus the display title, so a card and a candidate for the
    same concept land near each other regardless of body length."""
    return f"{norm_key} {title}".strip()


def _put_member(
    rm: IResourceManager,
    member_id: str,
    *,
    collection_id: str,
    kind: str,
    ref_id: str,
    run_id: str,
    norm_key: str,
    cluster_key: str,
    state: str,
    embedding: list[float] | None,
    reason: str = "",
    label: str = "",
) -> None:
    """Idempotently upsert one :class:`ClusterMember` by its deterministic id — the
    single write path shared by the finalize-time :class:`Reconciler` and the
    background :func:`backfill_collection` sweep, so both project members identically."""
    rm.create_or_update(
        member_id,
        ClusterMember(
            collection_id=collection_id,
            kind=kind,
            ref_id=ref_id,
            run_id=run_id,
            norm_key=norm_key,
            cluster_key=cluster_key,
            state=state,
            reason=reason,
            label=label,
            embedding=embedding,
        ),
    )


class Reconciler:
    """#506 P6: the finalize-time semantic reconcile. Projects a collection's
    existing cards + each run's candidates into :class:`ClusterMember` (carrying an
    embedding), then for every proposal decides — via :func:`grade_candidate` —
    whether it is already explained (``suppress``, auto-dropped but auditable),
    partially covered (``update`` an existing card), or genuinely new; and assigns a
    ``cluster_key`` (:func:`assign_cluster_key`) so duplicates across runs collapse
    to one inbox row (⑤). Thresholds are collection-wide config hyperparameters.

    ``wiki_text`` is an injected ``(collection_id) -> str`` provider returning the
    collection's whole wiki as one string, used by :meth:`reconcile_term_questions`
    ONLY: a term already written down anywhere needn't be asked of a human. Card
    proposals are deliberately NOT graded against it — see the note in
    :meth:`reconcile_proposals`. Loaded ONCE per batch (not per candidate) so the
    blob read isn't repeated; ``None`` disables the wiki check. See
    :func:`collection_wiki_text`."""

    def __init__(
        self,
        spec: SpecStar,
        embedder: Embedder,
        *,
        cluster_tau: float = 0.9,
        suppress_tau: float = 0.92,
        update_tau: float = 0.8,
        wiki_text: Callable[[str], str] | None = None,
    ) -> None:
        self._spec = spec
        self._embedder = embedder
        self._cluster_tau = cluster_tau
        self._suppress_tau = suppress_tau
        self._update_tau = update_tau
        self._wiki_text = wiki_text

    def reconcile_proposals(
        self,
        collection_id: str,
        run_id: str,
        proposals: list[ProposedCard],
        existing: Sequence[tuple[str, ContextCard]],
    ) -> list[ProposedCard]:
        """Grade + cluster a run's proposals against existing cards. Returns the
        proposals to KEEP on the run (suppressed ones are dropped here but recorded
        as auditable ``state="suppressed"`` members). Mutates ``update`` proposals'
        ``mode``/``target_card_id`` in place. Idempotent per (run, proposal) via a
        deterministic member id, so an accidental re-finalize can't double-count."""
        ensure_proposal_ids(proposals)
        self._project_cards(collection_id, existing)
        kept: list[ProposedCard] = []
        for p in proposals:
            norm_key = (derive_norm_keys(p.keys) or [""])[0]
            vec = self._embed(_card_text(norm_key, p.title))
            state = "active"
            reason = ""
            if p.mode == "new":
                # NOT graded against the wiki — only against existing CARDS. A card
                # and a wiki page are different sources, not substitutes (#537): the
                # card is the cheap deterministic exact-key lookup the KB agent is
                # told to reach for FIRST, the wiki is a reader sub-agent two tiers
                # up in cost. Suppressing the cheap source because the expensive one
                # mentions the term inverts that order, and made "generate cards from
                # a wiki page" a guaranteed no-op: every key drafted off a page is by
                # construction present in the corpus being greped. The wiki check
                # belongs on term QUESTIONS (don't ask what is already written down),
                # which is where it stays — see reconcile_term_questions.
                grade = grade_candidate(
                    self._spec,
                    collection_id=collection_id,
                    embedding=vec,
                    tau_high=self._suppress_tau,
                    tau_update=self._update_tau,
                )
                if grade.action == "suppress":
                    state, reason = "suppressed", grade.reason
                elif grade.action == "update":
                    p.mode = "update"
                    p.target_card_id = grade.target_card_id
            cluster_key = assign_cluster_key(
                self._spec,
                collection_id=collection_id,
                norm_key=norm_key,
                embedding=vec,
                tau=self._cluster_tau,
            )
            self._record(
                f"prop:{run_id}:{p.id}",
                collection_id=collection_id,
                kind="proposal",
                ref_id=p.id,
                run_id=run_id,
                norm_key=norm_key,
                cluster_key=cluster_key,
                state=state,
                embedding=vec,
                reason=reason,
                label=p.title or (p.keys[0] if p.keys else norm_key),
            )
            if state != "suppressed":
                kept.append(p)
        return kept

    def reconcile_term_questions(
        self,
        collection_id: str,
        items: Sequence[tuple[str, Callable[[], str]]],
    ) -> None:
        """Grade + cluster the term questions a run's digests raised, BEFORE they are
        opened (③⑥). For each ``(term, open_question)``: grade the term against the
        collection's wiki + existing cards (:func:`grade_candidate`); if it is already
        explained (a wiki grep hit or a near-duplicate of a card) → record a
        suppressed, auditable ClusterMember and **do NOT open the question** (so an
        already-answered term is never re-asked); otherwise open it
        (``open_question() -> qid``) and project an active member so it clusters with
        any proposal for the same concept (⑤).

        The wiki blob is loaded ONCE for the whole batch (not per term). Idempotent per
        opened question id, and per ``norm_key`` for a suppressed term. ``update``-range
        nearness does NOT suppress — a partially-covered term is still worth asking."""
        if not items:
            return
        wiki_blob = self._wiki_text(collection_id).lower() if self._wiki_text else ""
        for term, open_question in items:
            # A blank term is not a question — nothing to ask, nothing to audit.
            # The old substring net only swallowed it by accident (`"" in blob` is
            # True), so it leaked a blank question through whenever the collection
            # had no wiki; that must not depend on whether a wiki exists.
            if not term.strip():
                continue
            norm_key = norm(term)
            vec = self._embed(_card_text(norm_key, term))
            wiki = _wiki_mentions(wiki_blob, term)
            grade = grade_candidate(
                self._spec,
                collection_id=collection_id,
                embedding=vec,
                tau_high=self._suppress_tau,
                tau_update=self._update_tau,
                wiki_hit=wiki,
            )
            cluster_key = assign_cluster_key(
                self._spec,
                collection_id=collection_id,
                norm_key=norm_key,
                embedding=vec,
                tau=self._cluster_tau,
            )
            if grade.action == "suppress":
                self._record(
                    f"tq-sup:{collection_id}:{norm_key}",
                    collection_id=collection_id,
                    kind="term_question",
                    ref_id="",
                    run_id="",
                    norm_key=norm_key,
                    cluster_key=cluster_key,
                    state="suppressed",
                    embedding=vec,
                    reason=grade.reason,
                    label=term,
                )
                continue
            qid = open_question()
            self._record(
                f"tq:{qid}",
                collection_id=collection_id,
                kind="term_question",
                ref_id=qid,
                run_id="",
                norm_key=norm_key,
                cluster_key=cluster_key,
                state="active",
                embedding=vec,
                label=term,
            )

    def _project_cards(
        self, collection_id: str, existing: Sequence[tuple[str, ContextCard]]
    ) -> None:
        """Idempotently mirror the collection's cards into ClusterMember so the
        native nearest-card query has vectors to compare against. Re-embedded each
        finalize (cheap batch) so an edited card's vector stays fresh."""
        if not existing:
            return
        texts = [_card_text((card.norm_keys or [""])[0], card.title) for _, card in existing]
        vecs = self._embedder.embed_documents(texts)
        for (card_id, card), vec in zip(existing, vecs, strict=True):
            norm_key = (card.norm_keys or [""])[0]
            self._record(
                f"card:{card_id}",
                collection_id=collection_id,
                kind="card",
                ref_id=card_id,
                run_id="",
                norm_key=norm_key,
                cluster_key=norm_key,
                state="active",
                embedding=vec,
                label=card.title or (card.keys[0] if card.keys else norm_key),
            )

    def _embed(self, text: str) -> list[float]:
        return self._embedder.embed_documents([text])[0]

    def _record(
        self,
        member_id: str,
        *,
        collection_id: str,
        kind: str,
        ref_id: str,
        run_id: str,
        norm_key: str,
        cluster_key: str,
        state: str,
        embedding: list[float],
        reason: str = "",
        label: str = "",
    ) -> None:
        _put_member(
            self._spec.get_resource_manager(ClusterMember),
            member_id,
            collection_id=collection_id,
            kind=kind,
            ref_id=ref_id,
            run_id=run_id,
            norm_key=norm_key,
            cluster_key=cluster_key,
            state=state,
            embedding=embedding,
            reason=reason,
            label=label,
        )


def collection_wiki_text(spec: SpecStar, collection_id: str) -> str:
    """The collection's whole LLM wiki concatenated into one string — the grep
    corpus for the reconcile "already documented" net — or ``""`` when the
    collection has no wiki turned on (``Collection.use_wiki``). Sync + spec-only, so
    it is safe to call from the job-runner finalize step (no API / async deps)."""
    from specstar.types import ResourceIDNotFoundError

    try:
        coll = spec.get_resource_manager(Collection).get(collection_id).data
    except ResourceIDNotFoundError:
        return ""
    if not (isinstance(coll, Collection) and coll.use_wiki):
        return ""
    rm = spec.get_resource_manager(WikiPage)
    parts: list[str] = []
    for res in rm.list_resources((QB["collection_id"] == collection_id).build()):
        row = res.data
        assert isinstance(row, WikiPage)
        page = rm.restore_binary(row)
        data = page.content.data
        assert isinstance(data, bytes)
        parts.append(data.decode("utf-8", "ignore"))
    return "\n".join(parts)


def backfill_collection(
    spec: SpecStar,
    embedder: Embedder,
    collection_id: str,
    *,
    cluster_tau: float,
    limit: int = 200,
) -> int:
    """#506 P8: project a collection's pending proposals + open term questions that
    have NO :class:`ClusterMember` yet into active members — embed + assign a
    ``cluster_key`` (:func:`assign_cluster_key`) — so the grouped inbox clusters them
    instead of showing each as an un-grouped singleton.

    Members whose deterministic id already exists are skipped, so the sweep is
    idempotent (a second pass over an already-projected collection returns ``0``) and
    it never fights the finalize-time :class:`Reconciler` for rows it already wrote.
    Batched by ``limit`` so one sweep can't stall on a huge pre-P6 backlog — the
    remainder is picked up next tick. Returns the number of members newly projected."""
    rm = spec.get_resource_manager(ClusterMember)
    seen: set[str] = set()
    for r in rm.list_resources((QB["collection_id"] == collection_id).build()):
        seen.add(r.info.resource_id)  # ty: ignore[unresolved-attribute]
    n = 0
    # #511 P2: proposals live in first-class CardProposal rows now (not the nested
    # CardGenRun.proposals), so read the collection's ACTIVE proposals from there.
    for run_id, _created, p in CardProposalStore(spec).list_for_review(collection_id):
        member_id = f"prop:{run_id}:{p.id}"
        if member_id in seen:
            continue
        norm_key = (derive_norm_keys(p.keys) or [""])[0]
        vec = embedder.embed_documents([_card_text(norm_key, p.title)])[0]
        cluster_key = assign_cluster_key(
            spec,
            collection_id=collection_id,
            norm_key=norm_key,
            embedding=vec,
            tau=cluster_tau,
        )
        _put_member(
            rm,
            member_id,
            collection_id=collection_id,
            kind="proposal",
            ref_id=p.id,
            run_id=run_id,
            norm_key=norm_key,
            cluster_key=cluster_key,
            state="active",
            embedding=vec,
            label=p.title or (p.keys[0] if p.keys else norm_key),
        )
        seen.add(member_id)
        n += 1
        if n >= limit:
            return n
    for qid, _created, q in questions_by_status(spec, [collection_id], ["open"]):
        if q.kind != "term":
            continue
        member_id = f"tq:{qid}"
        if member_id in seen:
            continue
        norm_key = q.norm_key or norm(q.term)
        vec = embedder.embed_documents([_card_text(norm_key, q.term)])[0]
        cluster_key = assign_cluster_key(
            spec,
            collection_id=collection_id,
            norm_key=norm_key,
            embedding=vec,
            tau=cluster_tau,
        )
        _put_member(
            rm,
            member_id,
            collection_id=collection_id,
            kind="term_question",
            ref_id=qid,
            run_id="",
            norm_key=norm_key,
            cluster_key=cluster_key,
            state="active",
            embedding=vec,
            label=q.term,
        )
        seen.add(member_id)
        n += 1
        if n >= limit:
            return n
    return n


def _find(parent: dict[str, str], x: str) -> str:
    """Union-find root with path halving — the disjoint-set core of the merge sweep."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def merge_near_clusters(
    spec: SpecStar,
    collection_id: str,
    *,
    merge_tau: float,
    limit: int = 100,
) -> int:
    """#506 P8: union clusters whose CENTROIDS are within ``merge_tau`` cosine
    similarity into one — healing the parallel-race split where two finalize passes
    opened two keys ("widget" / "widgets") for one concept before either could see the
    other's member. Deterministic: the canonical key per merged group is the one
    carrying the most members (ties → lexicographically smallest), and every other
    member is rewritten to it. Idempotent (a converged collection returns ``0``) and
    batched by ``limit`` absorbed clusters so a huge fan-out can't stall one sweep.
    Returns the number of clusters folded into another."""
    rm = spec.get_resource_manager(ClusterMember)
    rows: list[tuple[str, ClusterMember]] = []
    for r in rm.list_resources((QB["collection_id"] == collection_id).build()):
        m = r.data
        assert isinstance(m, ClusterMember)
        rows.append((r.info.resource_id, m))  # ty: ignore[unresolved-attribute]
    # Centroid per cluster_key = mean of its members' embeddings (skip keyless / vecless).
    sums: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for _rid, m in rows:
        if not m.cluster_key or m.embedding is None:
            continue
        acc = sums.get(m.cluster_key)
        if acc is None:
            sums[m.cluster_key] = list(m.embedding)
        else:
            for i, v in enumerate(m.embedding):
                acc[i] += v
        counts[m.cluster_key] = counts.get(m.cluster_key, 0) + 1
    keys = sorted(sums)  # deterministic pair-scan order
    centroids = {k: [v / counts[k] for v in sums[k]] for k in keys}
    # Union every pair of clusters within τ.
    parent = {k: k for k in keys}
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if 1.0 - cosine_distance(centroids[keys[i]], centroids[keys[j]]) >= merge_tau:
                parent[_find(parent, keys[j])] = _find(parent, keys[i])
    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(_find(parent, k), []).append(k)
    # Canonical per group (most members, then lexicographically smallest), then rewrite
    # every non-canonical member — group-atomic, capped at `limit` absorbed clusters.
    rows_by_key: dict[str, list[tuple[str, ClusterMember]]] = {}
    for rid, m in rows:
        rows_by_key.setdefault(m.cluster_key, []).append((rid, m))
    absorbed = 0
    for group in sorted(groups.values(), key=lambda g: sorted(g)):
        losers = [k for k in group]
        if len(losers) < 2 or absorbed >= limit:
            continue
        best = min(losers, key=lambda k: (-counts[k], k))
        for k in losers:
            if k == best:
                continue
            for rid, m in rows_by_key.get(k, []):
                _put_member(
                    rm,
                    rid,
                    collection_id=m.collection_id,
                    kind=m.kind,
                    ref_id=m.ref_id,
                    run_id=m.run_id,
                    norm_key=m.norm_key,
                    cluster_key=best,
                    state=m.state,
                    embedding=m.embedding,
                    reason=m.reason,
                    label=m.label,
                )
            absorbed += 1
    return absorbed


@dataclass(frozen=True)
class SweepReport:
    """What one :func:`sweep_clusters` pass did across the whole store — how many
    orphan candidates were backfilled into members and how many race-split clusters
    were folded. Summed over every collection; a converged store reports ``(0, 0)``."""

    backfilled: int = 0
    merged: int = 0


def sweep_clusters(
    spec: SpecStar,
    embedder: Embedder,
    *,
    cluster_tau: float,
    merge_tau: float,
    limit: int = 200,
) -> SweepReport:
    """#506 P8: the periodic maintenance pass — for EVERY collection, backfill its
    un-projected pending proposals / open questions (:func:`backfill_collection`) then
    fold its race-split clusters (:func:`merge_near_clusters`), so the grouped inbox
    converges without a reindex. Per-collection errors are swallowed so one bad
    collection never stalls the sweep (a cascaded-away collection, a transient embed
    failure); both passes are idempotent, so the API sweeper can run it on a timer.
    Returns the store-wide totals."""
    rm = spec.get_resource_manager(Collection)
    backfilled = 0
    merged = 0
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        cid = r.info.resource_id  # ty: ignore[unresolved-attribute]
        try:
            backfilled += backfill_collection(
                spec, embedder, cid, cluster_tau=cluster_tau, limit=limit
            )
            merged += merge_near_clusters(spec, cid, merge_tau=merge_tau, limit=limit)
        except Exception:  # noqa: BLE001 — one bad collection must not stall the sweep
            continue
    return SweepReport(backfilled=backfilled, merged=merged)
