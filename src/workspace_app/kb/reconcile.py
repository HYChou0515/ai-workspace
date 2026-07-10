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

from specstar import QB, SpecStar
from specstar.util.vector_distance import cosine_distance

from ..resources.kb import ClusterMember, Collection, ContextCard, WikiPage
from .card_gen import ProposedCard, ensure_proposal_ids
from .context_cards import derive_norm_keys
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
    """The reconcile verdict for one candidate against the collection's existing
    cards + wiki. ``action`` is ``suppress`` (already explained → auto-drop, kept
    only as an auditable ClusterMember), ``update`` (partially covered → suggest
    editing ``target_card_id``), or ``new`` (a genuinely new concept). ``reason``
    records WHY a suppress fired (``wiki`` grep hit vs ``near-card``) for the audit
    view."""

    action: str  # "suppress" | "update" | "new"
    target_card_id: str | None = None
    reason: str = ""  # "wiki" | "near-card" | ""


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


class Reconciler:
    """#506 P6: the finalize-time semantic reconcile. Projects a collection's
    existing cards + each run's candidates into :class:`ClusterMember` (carrying an
    embedding), then for every proposal decides — via :func:`grade_candidate` —
    whether it is already explained (``suppress``, auto-dropped but auditable),
    partially covered (``update`` an existing card), or genuinely new; and assigns a
    ``cluster_key`` (:func:`assign_cluster_key`) so duplicates across runs collapse
    to one inbox row (⑤). Thresholds are collection-wide config hyperparameters.

    ``wiki_text`` is an injected ``(collection_id) -> str`` provider returning the
    collection's whole wiki as one string (the deterministic "already documented in
    the wiki" safety net — a candidate whose surface key appears there is
    suppressed). Loaded ONCE per finalize (not per candidate) so the blob read isn't
    repeated; ``None`` disables the wiki check. See :func:`collection_wiki_text`."""

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
        # Load the collection's wiki ONCE (not per candidate); "" when disabled.
        wiki_blob = self._wiki_text(collection_id).lower() if self._wiki_text and proposals else ""
        kept: list[ProposedCard] = []
        for p in proposals:
            norm_key = (derive_norm_keys(p.keys) or [""])[0]
            vec = self._embed(_card_text(norm_key, p.title))
            # Already documented in the wiki? A hit on ANY surface key (substring,
            # case-insensitive — same default as the search_wiki tool).
            wiki = bool(wiki_blob) and any(k.strip() and k.lower() in wiki_blob for k in p.keys)
            state = "active"
            if p.mode == "new":
                grade = grade_candidate(
                    self._spec,
                    collection_id=collection_id,
                    embedding=vec,
                    tau_high=self._suppress_tau,
                    tau_update=self._update_tau,
                    wiki_hit=wiki,
                )
                if grade.action == "suppress":
                    state = "suppressed"
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
            )
            if state != "suppressed":
                kept.append(p)
        return kept

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
    ) -> None:
        rm = self._spec.get_resource_manager(ClusterMember)
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
                embedding=embedding,
            ),
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
