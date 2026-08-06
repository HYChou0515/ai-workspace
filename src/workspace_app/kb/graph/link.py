"""#534 B / #697 — bring the stored vocabulary up to date with the evidence.

The DECISIONS live in :mod:`build`, which holds no store handle. This module is
the errand around them: read the evidence out of the store a page at a time,
hand it over, and record what comes back. It is what production runs, and the
read-only inspection tool runs the same computation without the two ends.

Splitting it that way is the point of #697. The pass used to create identities
while it queried for them, so there was no way to ask "what would a different
extraction criterion produce?" without writing the answer into the corpus you
were judging.

Reads are bounded and come off the INDEX. Every field the vocabulary looks at is
indexed precisely so a corpus-wide walk never materialises a row, and pages are
capped because `list_resources(QB.all())` asks for the whole table in one
statement — 40k rows is a 937 KB statement Postgres answers with `stack depth
limit exceeded` (#689).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from specstar import QB, SpecStar

from ...resources.graph import (
    GraphEntityLink,
    GraphMention,
    GraphRelationship,
)
from ..llm import ILlm
from .build import Decisions, MentionEvidence, RelationEvidence, build_vocabulary
from .paging import PAGE, indexed_rows
from .persist import persist_vocabulary

__all__ = [
    "indexed_rows",  # re-exported: it moved to `paging` so `persist` can use it too
    "read_decisions",
    "read_mention_evidence",
    "read_relation_evidence",
    "reconcile_vocabulary",
]

_LOGGER = logging.getLogger(__name__)


_MENTION_FIELDS = (
    "source_doc_id",
    "norm_surface",
    "surface",
    "norm_kind",
    "kind",
    "occurrences",
    "collection_id",
    "declared_same_as",
)
_RELATION_FIELDS = ("norm_predicate", "predicate", "collection_id")


def read_mention_evidence(spec: SpecStar) -> list[MentionEvidence]:
    """Every mention, as the handful of values the vocabulary reads.

    Two reads, and the second is usually EMPTY. The indexed walk covers the whole
    corpus without touching a blob; the only thing it cannot serve is
    ``declared_quote``, which no other reader wants and which would cost an index
    on every row to serve the handful that carry a declaration. So the rows whose
    INDEXED ``declared_same_as`` is non-empty — and only those — are fetched
    properly afterwards.

    Non-empty, not ``is_not_null``: the field defaults to ``[]``, which is not
    null, so a null test matches every mention in the corpus and quietly turns
    the whole walk back into one decode per row.
    """
    rm = spec.get_resource_manager(GraphMention)
    out: dict[str, MentionEvidence] = {}
    declaring: list[str] = []
    for rid, values in indexed_rows(rm, _MENTION_FIELDS):
        out[rid] = MentionEvidence(
            id=rid,
            norm_surface=str(values.get("norm_surface") or ""),
            surface=str(values.get("surface") or ""),
            collection_id=str(values.get("collection_id") or ""),
            source_doc_id=str(values.get("source_doc_id") or ""),
            norm_kind=str(values.get("norm_kind") or ""),
            kind=str(values.get("kind") or ""),
            occurrences=int(values.get("occurrences") or 0),
            declared_same_as=tuple(values.get("declared_same_as") or ()),
        )
        if values.get("declared_same_as"):
            declaring.append(rid)
    for start in range(0, len(declaring), PAGE):
        page = declaring[start : start + PAGE]
        for r in rm.list_resources((QB.resource_id() << page).build()):
            mention = r.data
            assert isinstance(mention, GraphMention)
            known = out[r.info.resource_id]  # ty: ignore[unresolved-attribute]
            out[r.info.resource_id] = MentionEvidence(  # ty: ignore[unresolved-attribute]
                id=known.id,
                norm_surface=known.norm_surface,
                surface=known.surface,
                collection_id=known.collection_id,
                source_doc_id=mention.source_doc_id,
                norm_kind=known.norm_kind,
                kind=known.kind,
                occurrences=known.occurrences,
                declared_same_as=tuple(mention.declared_same_as),
                declared_quote=mention.declared_quote,
            )
    return list(out.values())


def read_decisions(spec: SpecStar) -> Decisions:
    """What people have already answered about pairs of identities.

    Read off the INDEX (`state` and `proposed_from` are both indexed), because
    this asks one question about every link in the corpus and reads nothing else
    off the row.

    `accept_proposal` marks the pair `settled` and `reject_proposal` marks it
    `rejected`; both keep `proposed_from`, which is what makes the pair
    recoverable. Feeding them back is what lets the vocabulary be recomputed from
    scratch without losing — or half-applying — a decision a person made.
    """
    rm = spec.get_resource_manager(GraphEntityLink)
    accepted: set[tuple[str, str, str]] = set()
    rejected: set[tuple[str, str]] = set()
    # `entity_id` is REQUESTED, not merely read: `indexed_rows` falls back to
    # the blob only for rows missing a field it was asked for, so reading a
    # field off the side means a row written before that index silently
    # yields nothing — and a decision that reads as absent is a decision
    # asked again next week.
    settled: list[str] = []
    for rid, values in indexed_rows(rm, ("entity_id", "state", "proposed_from")):
        other = str(values.get("proposed_from") or "")
        if not other:
            continue
        pair = (str(values.get("entity_id") or ""), other)
        if not pair[0]:
            continue
        if values.get("state") == "settled":
            settled.append(rid)
        elif values.get("state") == "rejected":
            rejected.add(pair)
    # WHO agreed is on `evidence`, which is not indexed — it is read by nobody
    # else and would cost an index on every link to serve the few a person has
    # answered. So those rows, and only those, are fetched properly.
    for start in range(0, len(settled), PAGE):
        for r in rm.list_resources((QB.resource_id() << settled[start : start + PAGE]).build()):
            link = r.data
            assert isinstance(link, GraphEntityLink)
            accepted.add((link.entity_id, link.proposed_from, link.evidence))
    return Decisions(accepted=frozenset(accepted), rejected=frozenset(rejected))


def read_relation_evidence(spec: SpecStar) -> list[RelationEvidence]:
    """Every relationship, as the connecting word and where it was said."""
    rm = spec.get_resource_manager(GraphRelationship)
    return [
        RelationEvidence(
            norm_predicate=str(values.get("norm_predicate") or ""),
            predicate=str(values.get("predicate") or ""),
            collection_id=str(values.get("collection_id") or ""),
        )
        for _, values in indexed_rows(rm, _RELATION_FIELDS)
    ]


def _step(name: str, run: Callable[[], int]) -> int:
    """Run one pass of the reconcile, and make sure it says so either way.

    The queue that drives this keeps only ``str(e)`` — specstar's
    ``message_queue/simple.py`` records the message on the job row and drops the
    traceback — so anything not logged HERE is gone. In production that turned a
    real failure into one bare sentence after "starting the vocabulary pass over
    the whole corpus", with nothing to say which of the four passes had produced
    it, and the passes only logged on success, so the "starting" line was left
    dangling.

    The failing statement is pulled off the exception because for a
    planner-level database error (``stack depth limit exceeded``) the Python
    traceback only points at ``list_resources`` — it is the SQL that identifies
    the problem, and psycopg2 hangs it on the cursor where nothing downstream
    looks.
    """
    started = time.perf_counter()
    try:
        count = run()
    except Exception as exc:
        sql = getattr(getattr(exc, "cursor", None), "query", None)
        if isinstance(sql, bytes):
            sql = sql.decode("utf-8", errors="replace")
        _LOGGER.exception(
            "graph reconcile: FAILED in %s after %.1fs%s",
            name,
            time.perf_counter() - started,
            # Truncated because a statement built from one row-constructor per
            # row can run to hundreds of KB, which is the very defect being
            # diagnosed — the head of it is enough to recognise the shape.
            f" — the statement was: {sql[:4000]}" if sql else " (no statement on the error)",
        )
        raise
    _LOGGER.info("graph reconcile: %s -> %d (%.1fs)", name, count, time.perf_counter() - started)
    return count


def reconcile_vocabulary(spec: SpecStar, llm: ILlm | None = None) -> None:
    """Recompute the vocabulary from the evidence and store the result.

    Three stages, each named in the log, because a stage that never ran has to
    look different from one that ran and found nothing. Every count used to be
    computed and discarded, so in production the pass was indistinguishable
    whether it had executed, executed and produced nothing, or never been queued
    — the only way to tell was to open the database and find the tables empty,
    which is how a corpus can extract for weeks with no entity page and nothing
    anywhere saying so.

    ``llm=None`` skips the merge-proposal pass, so it can be turned off by
    configuration rather than by editing.
    """
    _LOGGER.info("graph reconcile: starting the vocabulary pass over the whole corpus")
    mentions: list[MentionEvidence] = []
    relationships: list[RelationEvidence] = []
    decisions = Decisions()

    def _read() -> int:
        nonlocal decisions
        mentions.extend(read_mention_evidence(spec))
        relationships.extend(read_relation_evidence(spec))
        decisions = read_decisions(spec)
        return len(mentions) + len(relationships)

    vocabulary = build_vocabulary([], [])

    def _build() -> int:
        nonlocal vocabulary
        vocabulary = build_vocabulary(mentions, relationships, llm=llm, decisions=decisions)
        return len(vocabulary.entities)

    counts: list[int] = []

    def _store() -> int:
        entities, links = persist_vocabulary(spec, vocabulary)
        counts.extend((entities, links))
        return entities

    _step("read_evidence", _read)
    _step("build_vocabulary", _build)
    _step("persist_vocabulary", _store)
    entities, links = counts
    proposed = sum(1 for link in vocabulary.links if link.state == "pending")
    _LOGGER.info(
        "graph reconcile: done — %d entities now exist over %d mentions; "
        "%d links, of which %d are merge proposals awaiting review%s",
        entities,
        len(mentions),
        links,
        proposed,
        "" if llm is not None else " (no llm wired: merge proposals skipped)",
    )
    if entities == 0:
        # Not an error — a corpus with no mentions yet reconciles to nothing.
        # Said out loud because "the entity page is empty" is otherwise
        # indistinguishable from the pass never having been queued at all.
        _LOGGER.warning(
            "graph reconcile: finished with NO entities — either nothing has been "
            "extracted yet, or every mention carries an empty norm_surface"
        )
