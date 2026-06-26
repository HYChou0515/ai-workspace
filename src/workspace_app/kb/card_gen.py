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
from typing import Protocol

import msgspec
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


class CardDrafter(Protocol):
    """The LLM-driven seam the generation job calls once per document: read the
    document's extracted text, return the glossary cards worth drafting from it.
    Production wraps an ``Llm`` + a classify prompt; tests inject a fake that
    returns canned drafts (so the job's orchestration is testable without a
    model)."""

    def draft(self, *, doc_path: str, doc_text: str) -> list[CardDraft]: ...


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
    """One generation run: draft cards for ``doc_ids`` (the documents the user
    selected by updated time, #175 Q2) within ``collection_id``."""

    collection_id: str
    doc_ids: list[str] = msgspec.field(default_factory=list)


class CardGenArtifact(msgspec.Struct):
    """The job's typed output: the reviewable card proposals."""

    proposals: list[ProposedCard] = msgspec.field(default_factory=list)


class CardGenJob(Job[CardGenPayload, CardGenArtifact]):
    """A queued context-card generation run. ``partition_key`` is set to the
    collection id at enqueue time so a collection's generation runs serialise
    across consumers (cross-pod, the framework's guarantee); ``status`` drives
    the live progress UI and ``artifact`` carries the proposals to review."""


class CommitResult(msgspec.Struct):
    """The outcome of committing a run's accepted proposals to real cards."""

    created: int = 0
    updated: int = 0
    skipped: int = 0


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
