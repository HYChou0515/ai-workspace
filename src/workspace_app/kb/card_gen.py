"""Automatic context-card generation (#175) — the data shapes + the
deterministic dedup/classify core for the "自動 context card" feature.

#175 is the lightweight, in-tab cousin of the deferred ``→collections``
workflow (#205): instead of an App-level workflow it runs a single specstar
job that reads a collection's selected documents, drafts glossary cards from
them (the LLM-driven ``CardDrafter``), and writes the proposals onto the job's
``artifact`` for a human to review before committing.

This module owns:

  - the **seam** (``CardDrafter``) the job calls per document — a fake stands in
    for the LLM in tests, the production drafter wraps an ``Llm``;
  - the job's **payload / artifact** structs (``CardGenPayload`` /
    ``CardGenArtifact``) and the ``CardGenJob`` itself (``partition_key`` = the
    collection id, so a collection's runs serialise across consumers); and
  - the **deterministic core** (``merge_drafts`` + ``classify_against_existing``)
    that dedups drafts by normalised key and decides new-vs-update-vs-skip
    against the collection's existing cards (#106 ``norm_keys``).

No embedding, no agent loop here: the LLM only drafts; everything that touches
the glossary's identity (``norm_keys`` membership) is deterministic so the same
documents always produce the same proposals.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Protocol

import msgspec
from specstar import OnDelete, Ref
from specstar.types import Job

from .context_cards import derive_norm_keys, norm


class CardDraft(msgspec.Struct):
    """One glossary card drafted from a single document by the ``CardDrafter``,
    before dedup against other drafts / existing cards. ``keys`` are the surface
    forms (term + aliases) the reader might search; ``confident`` is the
    classifier's self-rated certainty (an uncertain draft surfaces with a ⚠️ in
    review and is not committed by default, #205); ``snippet`` is the supporting
    passage the draft was derived from — the provenance a reviewer audits."""

    keys: list[str]
    title: str = ""
    body: str = ""
    confident: bool = True
    snippet: str = ""


class TermQuestionDraft(msgspec.Struct):
    """A term the digest couldn't confidently define from the document (#377) —
    raised as a question instead of a hallucinated card. ``term`` is the surface
    form the reader saw; ``question`` is what to ask the human. Its answer becomes
    a context card."""

    term: str
    question: str = ""


class DescriptionQuestionDraft(msgspec.Struct):
    """A passage the digest couldn't follow (#377), ``quote``d verbatim, with the
    focused ``question`` to ask the human. Its answer lands on the collection's
    clarification wiki page."""

    quote: str
    question: str = ""


class DocDigest(msgspec.Struct):
    """One document's full digest (#377): the cards the reader could confidently
    draft, plus the questions it raised instead of guessing — terms it couldn't
    define and passages it couldn't follow. The single LLM pass yields all three
    so the reader writes what it knows and asks what it doesn't in one go."""

    cards: list[CardDraft] = msgspec.field(default_factory=list)
    term_questions: list[TermQuestionDraft] = msgspec.field(default_factory=list)
    description_questions: list[DescriptionQuestionDraft] = msgspec.field(default_factory=list)


class CardDrafter(Protocol):
    """The LLM-driven seam the generation job calls once per document: read the
    document's extracted text and return its ``DocDigest`` — the confident cards
    plus the term / description questions it raised instead of guessing (#377).
    Production wraps an ``Llm`` + a classify prompt; tests inject a fake that
    returns a canned digest (so the job's orchestration is testable without a
    model)."""

    def digest(self, *, doc_path: str, doc_text: str) -> DocDigest: ...


class Provenance(msgspec.Struct):
    """Where a proposed card came from — the document (``doc_id`` + display
    ``path``) and the ``snippet`` that triggered the definition. Lives only on
    the proposal (the review surface shows it as the audit "依據"); it is never
    written onto the committed ``ContextCard`` (#106 keeps cards content-only)."""

    doc_id: str
    path: str
    snippet: str = ""


class ProposedCard(msgspec.Struct):
    """A merged, classified card proposal on the job's artifact. ``mode`` is
    ``new`` (no overlap with an existing card) or ``update`` (shares ≥1
    normalised key with ``target_card_id``); a draft fully covered by an existing
    card is dropped, never proposed. ``decision`` carries the reviewer's verdict
    (#175 Q7 resumable review state — persisted on the artifact, so leaving the
    page and returning restores progress)."""

    keys: list[str]
    title: str = ""
    body: str = ""
    confident: bool = True
    mode: str = "new"  # new | update
    target_card_id: str | None = None
    provenance: list[Provenance] = msgspec.field(default_factory=list)
    decision: str = "pending"  # pending | accepted | rejected


class CardGenPayload(msgspec.Struct):
    """One step of a generation run (#414 fan-out). ``kind`` routes the handler:

      - ``split`` (the enqueued job): plan the run — fan ``doc_ids`` out into one
        ``process`` job per doc (or, for ≤1 doc, run it inline);
      - ``process``: digest ONE doc (``doc_index`` into the run's ordered doc set)
        and stage it, then win the finalize gate if it's the last;
      - ``finalize``: merge + classify the staged digests into the run's proposals
        and raise the questions, exactly once.

    ``run_id`` is the :class:`CardGenRun` every step drives (its id is what
    ``enqueue`` returns + the FE polls); ``collection_id`` is carried so the split
    job's ``partition_key`` serialises a collection's runs across consumers."""

    collection_id: str
    doc_ids: list[str] = msgspec.field(default_factory=list)
    kind: str = "split"  # split | process | finalize
    run_id: str = ""
    doc_index: int = -1  # process: which doc in the run's ordered doc set


class CardGenArtifact(msgspec.Struct):
    """The job's typed output: the reviewable card proposals."""

    proposals: list[ProposedCard] = msgspec.field(default_factory=list)


class CardGenJob(Job[CardGenPayload, CardGenArtifact]):
    """A queued step of a context-card generation run (#414 fan-out). The enqueued
    ``split`` job carries ``partition_key = collection_id`` so a collection's runs
    serialise across consumers; the fanned-out ``process`` jobs carry
    ``partition_key = None`` so they parallelise freely across worker pods (the CAS
    join on :class:`CardGenRun`, not the queue, guards correctness). The reviewable
    output lives on the run (``CardGenRun.proposals``), NOT on the job artifact —
    the split job returns before any proposal exists."""


class CardGenRun(msgspec.Struct):  # → resource "card-gen-run"
    """Issue #414: the fan-out **join state** + FE-facing durable state for one
    context-card generation run. Mirrors :class:`workspace_app.resources.IndexRun`.

    A run over N documents is fanned out into N small ``CardGenJob(kind="process")``
    jobs (one per doc) so they parallelise across worker pods. This row is how the
    independent process jobs agree on "every doc is digested": the split job seeds
    ``total`` (the doc count); each process job idempotently records its doc index
    in ``done`` (or ``failed``); the finalize step runs exactly once, gated by the
    CAS-claimed ``finalized`` flag (set only when ``done ∪ failed`` covers every
    doc). Correctness rests on compare-and-swap against the etag, never on the
    queue's partition_key (which the RabbitMQ backend ignores).

    The split job cannot hold the reviewable output — it must return fast (it can't
    block a consumer thread for the whole LLM pass), so it COMPLETEs before any
    proposal exists. So this run — addressed by the id ``enqueue`` returns — is what
    the FE polls for ``status`` + ``proposals`` (the finalize step writes them here,
    and the resumable review state is re-saved here too). ``doc_ids`` is the ordered
    doc set so finalize merges the staged per-doc digests deterministically."""

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    doc_ids: list[str] = msgspec.field(default_factory=list)
    total: int = 0
    done: list[int] = msgspec.field(default_factory=list)  # doc indices digested OK
    failed: list[int] = msgspec.field(default_factory=list)  # doc indices that gave up
    finalized: bool = False  # the exactly-once finalize gate (CAS-claimed)
    status: str = "pending"  # pending | running | done | error
    proposals: list[ProposedCard] = msgspec.field(default_factory=list)


class CardGenUnit(msgspec.Struct):  # → resource "card-gen-unit"
    """Issue #414 fan-out **staging**: one process job's digest of its document,
    id ``{run_id}.u{doc_index}``. The finalize step reads these back in doc order,
    merges + classifies their drafts into the run's proposals, and raises their
    questions — then deletes them. Transient, alive only between a run's process
    jobs and its finalize. Mirrors :class:`workspace_app.resources.IndexUnitText`."""

    run_id: Annotated[str, Ref("card-gen-run", on_delete=OnDelete.cascade)]
    doc_index: int
    doc_id: str = ""
    path: str = ""
    digest: DocDigest = msgspec.field(default_factory=DocDigest)


class CommitResult(msgspec.Struct):
    """The outcome of committing a run's accepted proposals to real cards."""

    created: int = 0
    updated: int = 0
    skipped: int = 0


class CardGenRunSummary(msgspec.Struct):
    """One row of a collection's 待審核 queue (#415): a finalized run awaiting
    review. The FE lists these and lazy-loads each run's proposals on expand."""

    run_id: str
    collection_id: str
    proposal_count: int


# ── deterministic core ───────────────────────────────────────────────────────


def merge_drafts(drafts: list[tuple[str, str, CardDraft]]) -> list[ProposedCard]:
    """Dedup raw drafts into proposals by NORMALISED key overlap (#205: "deduped
    by normalised key, aliases unioned"). Each draft is ``(doc_id, path, draft)``.
    Two drafts that share any ``norm_key`` merge into one proposal: their keys are
    unioned (dedup by ``norm``), their provenance accumulated. A draft with no
    usable key (blank after ``norm``) is dropped. A confident draft's title/body
    wins over an uncertain one's for the merged proposal; merge stays ``new`` here
    — overlap with existing cards is decided later by
    ``classify_against_existing``."""
    out: list[ProposedCard] = []
    norm_keys: list[set[str]] = []  # parallel to out: each proposal's norm_key set
    for doc_id, path, d in drafts:
        nks = set(derive_norm_keys(d.keys))
        if not nks:
            continue  # nothing lookup-able — can't become a findable card
        prov = Provenance(doc_id=doc_id, path=path, snippet=d.snippet)
        hit = next((i for i, ks in enumerate(norm_keys) if ks & nks), None)
        if hit is None:
            out.append(
                ProposedCard(
                    keys=list(d.keys),
                    title=d.title,
                    body=d.body,
                    confident=d.confident,
                    provenance=[prov],
                )
            )
            norm_keys.append(set(nks))
            continue
        p = out[hit]
        for k in d.keys:
            if norm(k) not in norm_keys[hit]:
                p.keys.append(k)
        norm_keys[hit] |= nks
        p.provenance.append(prov)
        if not p.confident and d.confident:  # a confident draft supersedes an uncertain body
            p.title, p.body, p.confident = d.title, d.body, True
    return out


def classify_against_existing(
    proposal: ProposedCard, existing: Sequence[tuple[str, object]]
) -> str | None:
    """Decide a proposal's fate against the collection's existing cards (#175
    Q5). ``existing`` is ``(card_id, ContextCard)`` pairs. Returns:

      - ``"skip"`` — the proposal's normalised keys are a subset of an existing
        card's (the term is already fully carded): a complete duplicate, dropped.
      - ``None`` after setting ``mode="update"`` + ``target_card_id`` — it shares
        ≥1 key with an existing card but adds something (partial overlap).
      - ``None`` leaving ``mode="new"`` — no overlap with any existing card.

    The first existing card it overlaps wins (cards rarely share keys)."""
    pnk = set(derive_norm_keys(proposal.keys))
    for card_id, card in existing:
        cnk = set(getattr(card, "norm_keys", []))
        inter = pnk & cnk
        if not inter:
            continue
        if pnk <= cnk:
            return "skip"  # fully covered by this card — a complete duplicate
        proposal.mode = "update"
        proposal.target_card_id = card_id
        return None
    return None  # stays "new"
