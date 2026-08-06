"""#697 — turn a corpus into a graph, without a database anywhere in the middle.

The graph could not be tuned. Extraction wipes and rewrites a document's rows
and the vocabulary pass builds identities by querying the store as it creates
them, so trying a different extraction criterion meant mutating the corpus you
were trying to judge — in an environment that had data in it. There was no safe
iteration loop, and that, rather than not knowing what to write, is what kept
the criterion unwritten.

So the computation is separated from the recording. Nothing in this module takes
a ``SpecStar`` handle: "it does not write" is not a promise someone has to keep,
there is simply nothing here to write with. What comes out is ordinary rows, and
two consumers take it from there — production hands them to a persist step, and
the read-only inspection tool dumps them as JSON. One core, two consumers, so
what you read in the JSON is what production produces.

Ids are not carried alongside the rows because they are DERIVED from them
(``mention_id``, ``relationship_id``): a row knows its own id, and a second copy
travelling beside it is a second place for it to be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...resources.graph import (
    GraphClaim,
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    GraphRelationship,
    entity_id,
    mention_id,
)
from ..llm import ILlm
from .entity_extract import Extraction, extract_entities
from .normalize import norm_attribute, norm_period, norm_surface, norm_unit


@dataclass(frozen=True)
class DocSource:
    """One document's text, and the read permission its rows must carry.

    ``mirror`` is the deck's effective read permission (``doc_mirror_fields``),
    read once by the caller — reading it is a query, and a query is the caller's
    business, not this module's. Empty is the fail-closed default the mirror
    fields already have: rows born without one are invisible rather than public.
    """

    doc_id: str
    chunks: list[tuple[str, str]]  # (chunk_id, text)
    mirror: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocGraph:
    """What one document yielded. Rows, ready to persist or to serialise."""

    mentions: list[GraphMention] = field(default_factory=list)
    claims: list[GraphClaim] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)


@dataclass(frozen=True)
class Graph:
    """A whole corpus's graph: the evidence and the identities built from it.

    What the read-only tool serialises. Production reaches the same two
    computations in two stages with the store in between — a corpus does not fit
    in one job — so this composition is the tool's, but the computation is
    shared, which is what makes the JSON a faithful preview.
    """

    mentions: list[GraphMention] = field(default_factory=list)
    claims: list[GraphClaim] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    entities: dict[str, GraphEntity] = field(default_factory=dict)
    links: list[GraphEntityLink] = field(default_factory=list)
    #: Whether the merge-proposal pass actually RAN. "The model proposed nothing"
    #: and "nobody asked the model" produce the same empty list, and a store told
    #: the first when the second is true deletes a review queue nobody answered.
    proposed: bool = False


def build_graph(
    llm: ILlm,
    docs: list[DocSource],
    *,
    collection_id: str,
    guidance: str = "",
    propose_with: ILlm | None = None,
) -> Graph:
    """Every document extracted, then the vocabulary built over all of it.

    ``propose_with`` is the model for the merge-proposal pass, off by default:
    it can only ADD work for a person, and when the question is what the
    EXTRACTOR did, its output is noise on top of the answer.
    """
    per_doc = [
        build_doc_graph(llm, doc, collection_id=collection_id, guidance=guidance) for doc in docs
    ]
    mentions = [m for doc in per_doc for m in doc.mentions]
    relationships = [r for doc in per_doc for r in doc.relationships]
    vocabulary = build_vocabulary(
        [evidence_of(m) for m in mentions],
        [relation_evidence_of(r) for r in relationships],
        llm=propose_with,
    )
    return Graph(
        mentions=mentions,
        claims=[c for doc in per_doc for c in doc.claims],
        relationships=relationships,
        entities=vocabulary.entities,
        links=vocabulary.links,
        # Carried, not defaulted. The flag exists precisely to separate "the
        # model proposed nothing" from "nobody asked it", so a copy of this
        # structure that always says the second is worse than no flag at all:
        # it looks like the answer and is not one.
        proposed=vocabulary.proposed,
    )


def build_doc_graph(
    llm: ILlm,
    doc: DocSource,
    *,
    collection_id: str,
    guidance: str = "",
) -> DocGraph:
    """Extract every chunk of ``doc`` and fold the result into its rows.

    Occurrences are aggregated across the WHOLE document rather than per passage:
    a deck naming the same tool on five slides is one row counted five times, and
    the count is the only signal anything downstream has for how much a document
    cared about something. Counted per passage it would say nothing.
    """
    extracted = [
        (chunk_id, extract_entities(llm, text, guidance=guidance)) for chunk_id, text in doc.chunks
    ]
    return DocGraph(
        mentions=_mentions(extracted, collection_id=collection_id, doc=doc),
        claims=_claims(extracted, collection_id=collection_id, doc=doc),
        relationships=_relationships(extracted, collection_id=collection_id, doc=doc),
    )


def _relationships(
    extracted: list[tuple[str, Extraction]],
    *,
    collection_id: str,
    doc: DocSource,
) -> list[GraphRelationship]:
    """What the document said connects two things, ends kept as surfaces."""
    return [
        GraphRelationship(
            collection_id=collection_id,
            source_doc_id=doc.doc_id,
            subject=rel.subject,
            predicate=rel.predicate,
            object=rel.object,
            norm_subject=norm_surface(rel.subject),
            norm_predicate=norm_surface(rel.predicate),
            norm_object=norm_surface(rel.object),
            chunk_id=chunk_id,
            quote=rel.quote,
            **doc.mirror,
        )
        for chunk_id, extraction in extracted
        for rel in extraction.relationships
    ]


def _claims(
    extracted: list[tuple[str, Extraction]],
    *,
    collection_id: str,
    doc: DocSource,
) -> list[GraphClaim]:
    """One row per statement, keeping the value exactly as the passage wrote it.

    The comparison keys are derived here rather than at read time because
    grouping reads ``indexed_data``: a key that only existed as a Python object
    could not be grouped on at all, and a filter on it would silently match
    nothing.
    """
    return [
        GraphClaim(
            collection_id=collection_id,
            source_doc_id=doc.doc_id,
            chunk_id=chunk_id,
            norm_subject=norm_surface(claim.subject),
            subject=claim.subject,
            norm_attribute=norm_attribute(claim.attribute),
            attribute=claim.attribute,
            value=claim.value,
            norm_value=norm_surface(claim.value),
            period=claim.period,
            norm_period=norm_period(claim.period),
            unit=claim.unit,
            norm_unit=norm_unit(claim.unit),
            **doc.mirror,
        )
        for chunk_id, extraction in extracted
        for claim in extraction.attributes
    ]


def _mentions(
    extracted: list[tuple[str, Extraction]],
    *,
    collection_id: str,
    doc: DocSource,
) -> list[GraphMention]:
    """Fold every passage's mentions into one row per comparison key.

    The display form is the surface the DOCUMENT used most, ties broken on the
    surface itself — never a normalised string nobody wrote, and never "whichever
    passage came first", because nothing orders the chunk read: that would make
    the stored form follow the store's mood, and a diff of two previews would
    show churn that is not a change in the extraction at all. The first non-empty
    kind still wins, so a later passage that omitted it does not erase what an
    earlier one said.
    """
    seen_surfaces: dict[str, dict[str, int]] = {}
    kinds: dict[str, str] = {}
    counts: dict[str, int] = {}
    chunk_ids: dict[str, list[str]] = {}
    same_as: dict[str, set[str]] = {}
    quotes: dict[str, str] = {}
    for chunk_id, extraction in extracted:
        for mention in extraction.mentions:
            key = norm_surface(mention.surface)
            tally = seen_surfaces.setdefault(key, {})
            tally[mention.surface] = tally.get(mention.surface, 0) + 1
            if mention.kind and not kinds.get(key):
                kinds[key] = mention.kind
            counts[key] = counts.get(key, 0) + 1
            seen = chunk_ids.setdefault(key, [])
            if chunk_id not in seen:
                seen.append(chunk_id)
    # An equivalence the passage STATED, recorded on BOTH names. A declaration is
    # worth nothing if only one side is evidence — "回焊爐,以下簡稱 RO" cannot be
    # applied unless "RO" is also a row for the vocabulary to join — so a name
    # that appears only inside the declaration becomes a mention of it here.
    # It does NOT count as another occurrence: being named in an aside is not the
    # document dwelling on something, and occurrences is the importance signal.
    for chunk_id, extraction in extracted:
        for alias in extraction.aliases:
            for name, other in ((alias.a, alias.b), (alias.b, alias.a)):
                key = norm_surface(name)
                if key not in seen_surfaces:
                    seen_surfaces[key] = {name: 1}
                    counts[key] = 1
                    chunk_ids[key] = [chunk_id]
                same_as.setdefault(key, set()).add(norm_surface(other))
                quotes.setdefault(key, alias.quote)
    return [
        GraphMention(
            collection_id=collection_id,
            source_doc_id=doc.doc_id,
            surface=surface,
            norm_surface=key,
            kind=kinds.get(key, ""),
            norm_kind=norm_surface(kinds.get(key, "")),
            occurrences=counts[key],
            chunk_ids=chunk_ids[key],
            declared_same_as=sorted(same_as.get(key, ())),
            declared_quote=quotes.get(key, ""),
            **doc.mirror,
        )
        for key, surface in sorted(
            (key, _display_name(list(tally.items()))) for key, tally in seen_surfaces.items()
        )
    ]


# ── the vocabulary ───────────────────────────────────────────────────
#
# Deciding that two mentions name one thing has to see ALL the evidence, which
# is why this pass used to read the whole corpus out of the store and create
# identities as it walked — and why it could not be re-run to find out what a
# different extraction criterion would produce. Handed the evidence, it is an
# ordinary computation over lists.
#
# Everything is derived from the mentions, in a fixed order, so two runs over the
# same evidence produce byte-identical output. That is what makes two JSON dumps
# comparable: a diff then shows what the CHANGE did, not what the iteration
# order happened to be that day.


@dataclass(frozen=True)
class MentionEvidence:
    """The whole of what the vocabulary reads about one mention.

    Narrow on purpose, and not a ``GraphMention``. Every field here is INDEXED,
    which is what lets production walk a whole corpus out of the metadata search
    without a decode per row — a full listing of 40k rows is one statement the
    database refuses (#689). A partly-filled ``GraphMention`` would have carried
    the same six values while inviting the next reader to reach for a permission
    mirror that is silently empty.

    ``id`` is the mention's own id: production has it from the store, and the
    read-only tool derives it, so neither has to agree with the other about how
    it is computed.
    """

    id: str
    norm_surface: str
    surface: str
    collection_id: str
    source_doc_id: str = ""
    norm_kind: str = ""
    kind: str = ""
    occurrences: int = 1
    declared_same_as: tuple[str, ...] = ()
    declared_quote: str = ""


@dataclass(frozen=True)
class RelationEvidence:
    """What the vocabulary reads about one relationship — its connecting word and
    where the evidence lives. The ends are not read: resolving them to identities
    is a later question, and freezing it here would make this a second place
    identity lives."""

    norm_predicate: str
    predicate: str
    collection_id: str


def evidence_of(mention: GraphMention) -> MentionEvidence:
    """A computed mention, seen as the vocabulary sees a stored one."""
    return MentionEvidence(
        id=mention_id(mention.source_doc_id, mention.surface),
        norm_surface=mention.norm_surface,
        surface=mention.surface,
        collection_id=mention.collection_id,
        source_doc_id=mention.source_doc_id,
        norm_kind=mention.norm_kind,
        kind=mention.kind,
        occurrences=mention.occurrences,
        declared_same_as=tuple(mention.declared_same_as),
        declared_quote=mention.declared_quote,
    )


def relation_evidence_of(rel: GraphRelationship) -> RelationEvidence:
    return RelationEvidence(
        norm_predicate=rel.norm_predicate,
        predicate=rel.predicate,
        collection_id=rel.collection_id,
    )


@dataclass(frozen=True)
class Decisions:
    """What a PERSON has already said about a pair of identities.

    The vocabulary is recomputed from the evidence every run, and a person's
    answer is not in the evidence. Unless it is fed back in, the recompute either
    throws it away or half-applies it — the merge comes apart into a live
    duplicate holding nothing, the host loses the keys the merge gave it, and the
    pair can never be raised again because the settled row still occupies its
    address. All three were observed before this existed.

    So a decision is an INPUT, and the merge is re-derived every run rather than
    stored as a mutation somebody has to remember not to overwrite. Pairs are
    unordered pairs of ENTITY IDS, which are content-addressed on keys and
    therefore stable across re-runs.
    """

    #: ``(entity, other, who)`` — the pair, and the person who said so. The name
    #: travels with the decision because the link it produces has to say that a
    #: PERSON approved this, not that a document declared it: "declared" points at
    #: a sentence a doubter can go and read, and after an accepted merge there is
    #: no sentence.
    accepted: frozenset[tuple[str, str, str]] = frozenset()
    rejected: frozenset[tuple[str, str]] = frozenset()

    def decided(self, a: str, b: str) -> bool:
        pairs = {(x, y) for x, y, _ in self.accepted} | set(self.rejected)
        return (a, b) in pairs or (b, a) in pairs


@dataclass(frozen=True)
class Vocabulary:
    """The identities the evidence supports, and what vouches for each.

    An identity owns nothing — saying "these mentions are the same thing" is a
    LINK, so a wrong grouping costs a link rather than a record: the mentions
    stay exactly as their documents wrote them, the mistake is visible (an entry
    holding evidence that does not belong) and undoing it loses nothing.
    """

    entities: dict[str, GraphEntity] = field(default_factory=dict)
    links: list[GraphEntityLink] = field(default_factory=list)
    #: Whether the merge-proposal pass actually RAN. "The model proposed nothing"
    #: and "nobody asked the model" produce the same empty list, and a store told
    #: the first when the second is true deletes a review queue nobody answered.
    proposed: bool = False


def build_vocabulary(
    mentions: list[MentionEvidence],
    relationships: list[RelationEvidence],
    *,
    llm: ILlm | None = None,
    decisions: Decisions | None = None,
) -> Vocabulary:
    """Group the evidence into identities. No store, no queries, no writes."""
    decisions = decisions or Decisions()
    by_key: dict[str, list[MentionEvidence]] = {}
    for mention in mentions:
        if mention.norm_surface:
            by_key.setdefault(mention.norm_surface, []).append(mention)

    entities: dict[str, GraphEntity] = {}
    links: list[GraphEntityLink] = []

    # EVERY key the vocabulary knows, not only the ones something mentions: a
    # kind label and a connecting word are identities too, and a person can have
    # merged one of those.
    all_keys = (
        set(by_key)
        | {m.norm_kind for m in mentions if m.norm_kind}
        | {r.norm_predicate for r in relationships if r.norm_predicate}
    )
    key_of = {entity_id(key): key for key in all_keys}

    # Which key each key now answers to. A merge — declared by a document, or
    # accepted by a person — folds one into another and leaves the absorbed one a
    # tombstone, so EVERY reference to a key has to go through this: a reference
    # to an absorbed key points at a row holding no evidence, which the access
    # scope reads as invisible to everyone.
    pairs = [
        (mention.norm_surface, other)
        for group in by_key.values()
        for mention in group
        for other in mention.declared_same_as
    ]
    # A decision naming an identity whose evidence is gone is dropped: there is
    # nothing left to merge, and re-inventing the key would put a name in the
    # vocabulary that no document says.
    approved_by: dict[str, str] = {}
    preferred: set[str] = set()
    for a, b, who in sorted(decisions.accepted):
        # A pair a person ALSO rejected is not merged. The two answers contradict
        # each other and nothing here can tell which came last, so it takes the
        # side this module always takes when it cannot be sure: it may fail to
        # merge, it may never merge wrongly.
        if a not in key_of or b not in key_of:
            continue
        if (a, b) in decisions.rejected or (b, a) in decisions.rejected:
            continue
        pairs.append((key_of[a], key_of[b]))
        # The side the person ACTED ON. A merged group otherwise takes the
        # smallest key as its host, which is unrelated to what anyone was
        # looking at — so the page they had open became a tombstone the moment
        # they pressed accept, and the identity turned up under a different id.
        preferred.add(key_of[a])
        approved_by.setdefault(key_of[a], who)
        approved_by.setdefault(key_of[b], who)
    groups = _key_groups(all_keys, pairs, preferred)
    host_of = {key: host for host, keys in groups.items() for key in keys}

    # Kinds first, so a thing can point at one. A kind whose label some document
    # ALSO mentions as a thing lands on the same id — the key is the same, so the
    # same mechanism unifies them, which is the point of not giving kinds a
    # mechanism of their own.
    kinds: dict[str, list[MentionEvidence]] = {}
    for mention in mentions:
        if mention.norm_kind:
            kinds.setdefault(host_of.get(mention.norm_kind, mention.norm_kind), []).append(mention)
    for kind_key in sorted(kinds):
        labelled = kinds[kind_key]
        entities[entity_id(kind_key)] = GraphEntity(
            canonical_name=_display_name([(m.kind, m.occurrences) for m in labelled]),
            # The WHOLE group, always. Every pass writes the identity at the same
            # id, so any one of them writing only its own key silently drops the
            # keys a merge folded in — and the absorbed name then lives on no row.
            norm_keys=groups.get(kind_key, [kind_key]),
            # A kind's evidence is the mentions carrying the label. Without it the
            # kind holds no collections, and the scope hides an identity nothing
            # vouches for — so it would be invisible on every page.
            collection_ids=sorted({m.collection_id for m in labelled}),
        )

    for host_key, keys in sorted(groups.items()):
        group = [m for key in keys for m in by_key.get(key, ())]
        if not group:
            continue  # a kind- or predicate-only identity; those passes own it
        eid = entity_id(host_key)
        labels = {host_of.get(m.norm_kind, m.norm_kind) for m in group if m.norm_kind}
        entities[eid] = GraphEntity(
            canonical_name=_display_name([(m.surface, m.occurrences) for m in group]),
            norm_keys=keys,
            # One label only. Two documents disagreeing about what sort of thing
            # this is means the question is open, and picking one silently answers
            # it — the kind is a signal, never a gate.
            kind_id=entity_id(next(iter(labels))) if len(labels) == 1 else "",
            collection_ids=sorted(
                {m.collection_id for m in group}
                | set(entities[eid].collection_ids if eid in entities else ())
            ),
        )
        quote = next((m.declared_quote for m in group if m.declared_quote), "")
        # An identity a PERSON approved says so, on every link. "declared" means
        # a document stated it and the link quotes the sentence; after an
        # accepted merge there is no sentence, and recording one anyway leaves a
        # citation with nothing after the colon.
        approver = next((approved_by[key] for key in keys if key in approved_by), "")
        links.extend(
            GraphEntityLink(
                entity_id=eid,
                mention_id=m.id,
                # A mention that IS this identity by its own key rests on the
                # rule; one a declaration brought in rests on the sentence, and
                # the link carries it so a doubter can go and read it.
                basis="approved"
                if approver
                else ("identical" if m.norm_surface == host_key else "declared"),
                evidence=approver
                or (
                    "norm_surface"
                    if m.norm_surface == host_key
                    else f"{m.source_doc_id}: {m.declared_quote or quote}"
                ),
                state="active",
                collection_ids=[m.collection_id],
            )
            for m in group
        )

    # A predicate is a name for a KIND OF CONNECTION, and it has the same problem
    # a thing has — "造成" and "leads to" are one connection written two ways — so
    # it gets the same answer rather than a mechanism of its own. Relationships
    # keep their verbatim predicate; this only ensures something exists to
    # resolve it to.
    predicates: dict[str, list[RelationEvidence]] = {}
    for rel in relationships:
        if rel.norm_predicate:
            # Through the same resolution: a predicate word a document declared
            # equivalent to another must not be minted at the absorbed key.
            predicates.setdefault(host_of.get(rel.norm_predicate, rel.norm_predicate), []).append(
                rel
            )
    for key in sorted(predicates):
        using = predicates[key]
        eid = entity_id(key)
        known = entities.get(eid)
        entities[eid] = GraphEntity(
            canonical_name=known.canonical_name
            if known
            else _display_name([(r.predicate, 1) for r in using]),
            # The WHOLE group. Writing just this key throws away the keys a
            # merge folded in, and the absorbed name then lives on no row at all
            # (the tombstone holds none either), so every lookup by name misses
            # it — including the agent's own `lookup_entity`.
            norm_keys=groups.get(key, [key]),
            kind_id=known.kind_id if known else "",
            collection_ids=sorted(
                {r.collection_id for r in using} | set(known.collection_ids if known else ())
            ),
        )

    # Each absorbed key keeps a tombstone saying where it went. Outside the
    # mention loop, because a kind or a connecting word is an identity nothing
    # MENTIONS — only a person can merge one — and leaving those without a
    # tombstone means the id a person still holds 404s instead of saying where
    # its evidence moved to.
    for host_key, keys in sorted(groups.items()):
        host_eid = entity_id(host_key)
        if host_eid not in entities:
            continue
        for absorbed in keys:
            if absorbed == host_key:
                continue
            named = by_key.get(absorbed, ())
            entities[entity_id(absorbed)] = GraphEntity(
                canonical_name=_display_name([(m.surface, m.occurrences) for m in named])
                if named
                else absorbed,
                norm_keys=[],
                collection_ids=[],
                merged_into=host_eid,
            )

    if llm is not None:
        links.extend(_proposals(llm, entities, links, decisions, key_of))
    return Vocabulary(entities=entities, links=links, proposed=llm is not None)


# How many names go into one adjudication call. Bounded by the model's context,
# not by any belief about the data. A vocabulary larger than this is split, and a
# pair landing in different batches is simply not proposed — the batches are
# ordered by name so they are stable, and a missed proposal is a merge nobody
# suggested, which reads as two entries someone can still merge by hand.
_NAMES_PER_CALL = 60

_GROUP_PROMPT = (
    "Below is a list of terms taken from technical documents. Some are different "
    "names for the SAME thing — a translation, an abbreviation, a spelling "
    "variant, a fuller form of the same name.\n\n"
    "Group ONLY those. Everything else is different, including pairs that are "
    "closely related: a defect and the object it occurs on, a machine and another "
    "machine in the same line, a property and a thing that has it, a category and "
    "one of its members, and two different values.\n\n"
    "For each group, the reason must say WHAT THE THING IS, in a few words, so a "
    'reader can check it — "the machine that prints solder paste", not "synonyms" '
    'or "related terms". If you cannot say what it is, the terms do not belong '
    "together.\n\n"
    "Terms:\n{names}\n\n"
    'Answer ONLY as JSON: {{"groups": [{{"names": ["…", "…"], "why": "<what it is>"}}]}}. '
    "An empty list is the right answer when nothing belongs together.\n\n"
    "A wrong grouping is expensive: it merges two different things into one and "
    "everything recorded about either becomes attached to both. Leave a pair out "
    "when unsure."
)


def _proposals(
    llm: ILlm,
    entities: dict[str, GraphEntity],
    links: list[GraphEntityLink],
    decisions: Decisions,
    key_of: dict[str, str],
) -> list[GraphEntityLink]:
    """Ask the model which names denote one thing, and record the answer as
    PENDING links that change nothing.

    The whole list at once, not pair by pair. Asking about pairs costs a call per
    pair — N² of them — which is what forced the earlier attempts to invent a
    cheap test for which pairs deserved one, and every such test was wrong within
    a corpus or two: character overlap admitted "condition" against "dose"
    because Latin has twenty-six letters, and a rule refusing to merge terms whose
    digits differ also refused 第2型糖尿病 against 第二型糖尿病. Rules over
    spelling keep meeting exceptions because the question is not about spelling.
    """
    live = {eid: e for eid, e in entities.items() if e.collection_ids}
    if len(live) < 2:
        return []
    by_name: dict[str, list[str]] = {}
    for eid, entity in live.items():
        by_name.setdefault(entity.canonical_name, []).append(eid)
    # Everything built so far is active by construction — a proposal is what
    # THIS function produces, and it produces them into its own list. A state
    # filter here would be a branch no input can take.
    active: dict[str, list[GraphEntityLink]] = {}
    for link in links:
        active.setdefault(link.entity_id, []).append(link)

    names = sorted(by_name)
    out: list[GraphEntityLink] = []
    seen: set[tuple[str, str]] = set()
    for start in range(0, len(names), _NAMES_PER_CALL):
        batch = names[start : start + _NAMES_PER_CALL]
        for group, why in _group(llm, batch):
            # Ordered by the identity's KEY — the same rule the grouping uses
            # to pick a merged group's host. Two orderings meant the side a
            # person accepted ON was not the side the merge landed on, so the id
            # they had just answered about became a tombstone and its page went
            # empty. Not the display name either: that is whichever surface the
            # corpus used MOST, so one new document reorders the pair and the
            # proposal becomes a different question from the one already answered.
            ids = sorted(
                {eid for name in group for eid in by_name.get(name, [])},
                key=lambda eid: key_of.get(eid, eid),
            )
            host, rest = ids[0], ids[1:]
            for other in rest:
                if (host, other) in seen or (other, host) in seen:
                    continue
                if decisions.decided(host, other):
                    continue  # a person already answered this; asking again is how
                    # a review queue stops being read
                seen.add((host, other))
                out.extend(_propose(host, other, live[other], active.get(other, []), why))
    return out


def _propose(
    host: str,
    other: str,
    entity: GraphEntity,
    other_links: list[GraphEntityLink],
    why: str,
) -> list[GraphEntityLink]:
    """One proposal, expressed over the other side's mentions.

    An identity with no links of its own still gets one — a single pending row
    naming no mention. A kind and a predicate ARE identities, but nothing MENTIONS
    「機台」: things are LABELLED with it. Expressing every proposal over the other
    side's mentions therefore made those two kinds of identity unproposable, and
    the taxonomy stayed split by language while the design said it would not.
    """
    if other_links:
        return [
            GraphEntityLink(
                entity_id=host,
                mention_id=link.mention_id,
                basis="resembles",
                evidence=why,
                state="pending",
                proposed_from=other,
                collection_ids=list(link.collection_ids),
            )
            for link in other_links
        ]
    # The proposal needs the collections its subject's evidence lives in, or the
    # scope reads "nothing vouches for this" and hides it from everyone — the
    # same fail-closed rule that has caught rows created without one three times.
    return [
        GraphEntityLink(
            entity_id=host,
            mention_id="",
            basis="resembles",
            evidence=why,
            state="pending",
            proposed_from=other,
            collection_ids=list(entity.collection_ids),
        )
    ]


def _group(llm: ILlm, names: list[str]) -> list[tuple[list[str], str]]:
    """The model's groupings, or nothing for an unreadable answer.

    Nothing, rather than a guess: this path can only ADD work for a person, so a
    confused reply should ask them nothing at all.
    """
    import json

    reply = llm.collect(_GROUP_PROMPT.format(names="\n".join(f"- {n}" for n in names)))
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    groups = data.get("groups") if isinstance(data, dict) else None
    out: list[tuple[list[str], str]] = []
    for item in groups if isinstance(groups, list) else []:
        if not isinstance(item, dict):
            continue
        members = [str(n) for n in item.get("names", []) if str(n) in names]
        if len(members) < 2:
            continue  # a group of one groups nothing; a name it invented is not ours
        out.append((sorted(set(members)), str(item.get("why", "")).strip()))
    return out


def _display_name(candidates: list[tuple[str, int]]) -> str:
    """The surface the corpus used most — a name someone actually wrote, never a
    normalised string nobody did. Ties break on the surface itself so a re-run
    cannot shuffle the name."""
    return max(candidates, key=lambda pair: (pair[1], pair[0]))[0]


def _key_groups(
    all_keys: set[str], pairs: list[tuple[str, str]], preferred: set[str] | None = None
) -> dict[str, list[str]]:
    """Fold every pair of keys that should be one identity into one group each.

    Returns ``host key -> every key that resolves there``. The host is the
    smallest key in the group, chosen for no reason but determinism: it fixes
    which id the identity gets, so two runs over the same evidence produce the
    same rows and a diff of two JSON dumps shows what the change did.

    A pair naming a key nothing produced is ignored. Nothing vouches for that
    name, so there is nothing to join — and inventing an identity for it would
    put a name in the vocabulary that no document said.
    """
    parent: dict[str, str] = {key: key for key in all_keys}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for left, right in pairs:
        if left not in parent or right not in parent:
            continue
        a, b = find(left), find(right)
        if a != b:
            parent[min(a, b)], parent[max(a, b)] = min(a, b), min(a, b)

    collected: dict[str, list[str]] = {}
    for key in all_keys:
        collected.setdefault(find(key), []).append(key)

    # The host is the smallest key — arbitrary, but fixed, so two runs over the
    # same evidence produce the same ids. A key a person acted on WINS that
    # choice: the merge has to land where they were looking, or the page they
    # just pressed accept on becomes a tombstone.
    wanted = preferred or set()
    groups: dict[str, list[str]] = {}
    for keys in collected.values():
        members = sorted(keys)
        chosen = sorted(wanted & set(members))
        groups[chosen[0] if chosen else members[0]] = members
    return groups
