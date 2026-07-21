"""#534 B — deciding the proposals, and reading the vocabulary back.

The review queue is the only place this system asks a person for attention, so
everything here is about not wasting it. A decision is remembered, so a question
already answered is never asked again — re-proposing a rejected pair would
re-spend the model AND put the same question back every week, which is exactly
how a queue stops being read.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass

import msgspec
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources.graph import (
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    GraphRelationship,
)


@dataclass(frozen=True)
class Evidence:
    """One place a name actually appeared, in the document's own words."""

    source_doc_id: str
    surface: str
    text: str


@dataclass(frozen=True)
class Proposal:
    """One pending merge, with both sides and the reason already in hand — a
    reviewer fetching each side separately would make the queue unusable at any
    volume."""

    entity_id: str
    proposed_from: str
    name: str
    other_name: str
    why: str
    # What each side actually looked like in the documents. Measured against a
    # real model, `why` is the least trustworthy thing here — it justified merging
    # two different machines with a sentence that read perfectly and described
    # only one of them. A reviewer needs the documents' own words, not the model's
    # account of them.
    evidence: list[Evidence]
    other_evidence: list[Evidence]


@dataclass(frozen=True)
class Related:
    """One connection this identity takes part in, from this identity's side."""

    direction: str  # "out" (subject) | "in" (object)
    predicate: str  # verbatim, as the document wrote it
    other_name: str  # the display name of the far end
    other_entity_id: str  # "" when the far end is not in the vocabulary yet
    quote: str
    source_doc_id: str
    chunk_id: str


@dataclass(frozen=True)
class EntityPage:
    """One identity and every piece of evidence for it, across documents."""

    entity: GraphEntity
    mentions: list[GraphMention]
    links: list[GraphEntityLink]
    occurrences: int
    related: list[Related]


def list_proposals(spec: SpecStar, *, as_user: str | None = None) -> list[Proposal]:
    """Every merge waiting on a person, one row per pair rather than per mention.

    A proposal is stored as pending links (so accepting it is the same absorption
    every other basis performs), but a reviewer is answering ONE question about
    two identities, and showing them the same question once per mention would bury
    it.

    With ``as_user`` the reads run inside the access scope, so a reviewer is never
    shown a pair they could not have looked at — including the identity on the far
    side of the proposal, which they would otherwise learn the name of.
    """
    lrm = spec.get_resource_manager(GraphEntityLink)
    erm = spec.get_resource_manager(GraphEntity)
    seen: set[tuple[str, str]] = set()
    out: list[Proposal] = []
    with ExitStack() as stack:
        if as_user is not None:
            stack.enter_context(lrm.using(as_user, apply_access_scope=True))  # ty: ignore[unknown-argument]
            stack.enter_context(erm.using(as_user, apply_access_scope=True))  # ty: ignore[unknown-argument]
        out.extend(_gather_proposals(spec, lrm, erm, seen))
    return out


def _gather_proposals(spec: SpecStar, lrm, erm, seen: set[tuple[str, str]]) -> list[Proposal]:
    out: list[Proposal] = []
    for r in lrm.list_resources((QB["state"] == "pending").build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        pair = (link.entity_id, link.proposed_from)
        if not link.proposed_from or pair in seen:
            continue
        seen.add(pair)
        try:
            host = erm.get(link.entity_id).data
            other = erm.get(link.proposed_from).data
        except ResourceIDNotFoundError:
            continue  # one side is out of this reader's reach
        assert isinstance(host, GraphEntity) and isinstance(other, GraphEntity)
        out.append(
            Proposal(
                entity_id=link.entity_id,
                proposed_from=link.proposed_from,
                name=host.canonical_name,
                other_name=other.canonical_name,
                why=link.evidence,
                evidence=_evidence_for(spec, link.entity_id),
                other_evidence=_evidence_for(spec, link.proposed_from),
            )
        )
    return out


# Enough for a reviewer to recognise the thing; more would turn a queue into
# reading material.
_EVIDENCE_PER_SIDE = 3


def _evidence_for(spec: SpecStar, entity_id: str) -> list[Evidence]:
    """A few places this identity actually appeared, in the documents' own words.

    The chunk is read without its own scope check because the MENTION was already
    filtered by one and a chunk belongs to the same document — reaching it means
    the caller could already read what it says.
    """
    from ...resources.kb import DocChunk

    lrm = spec.get_resource_manager(GraphEntityLink)
    mrm = spec.get_resource_manager(GraphMention)
    crm = spec.get_resource_manager(DocChunk)
    out: list[Evidence] = []
    for r in lrm.list_resources((QB["entity_id"] == entity_id).build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        if link.state != "active" or not link.mention_id or len(out) >= _EVIDENCE_PER_SIDE:
            continue
        try:
            mention = mrm.get(link.mention_id).data
        except ResourceIDNotFoundError:
            continue
        assert isinstance(mention, GraphMention)
        text = ""
        for chunk_id in mention.chunk_ids:
            try:
                chunk = crm.get(chunk_id).data
            except ResourceIDNotFoundError:
                continue
            if isinstance(chunk, DocChunk):
                text = chunk.text
                break
        out.append(
            Evidence(source_doc_id=mention.source_doc_id, surface=mention.surface, text=text)
        )
    return out


def accept_proposal(spec: SpecStar, entity_id: str, proposed_from: str, *, by: str) -> None:
    """Apply a proposal a person agreed with.

    The links become ``approved`` and record WHO agreed — the basis a later
    re-run treats as settled, and the answer to "why are these one thing" being a
    person's name rather than a model's impression.
    """
    from .link import _absorb  # noqa: PLC0415 — one merge implementation, not two

    _absorb(spec, entity_id, proposed_from, evidence=by)
    lrm = spec.get_resource_manager(GraphEntityLink)
    for r in lrm.list_resources((QB["state"] == "pending").build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        if (link.entity_id, link.proposed_from) != (entity_id, proposed_from):
            continue
        lrm.update(
            r.info.resource_id,  # ty: ignore[unresolved-attribute]
            msgspec.structs.replace(link, state="settled", basis="approved", evidence=by),
        )
    for r in lrm.list_resources((QB["entity_id"] == entity_id).build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        if link.state != "active":
            continue
        lrm.update(
            r.info.resource_id,  # ty: ignore[unresolved-attribute]
            msgspec.structs.replace(link, basis="approved", evidence=by),
        )


def reject_proposal(spec: SpecStar, entity_id: str, proposed_from: str, *, by: str) -> None:
    """Record that a person said no. Nothing merges — nothing was changed to
    propose it — but the answer is KEPT, so the pair is never raised again."""
    lrm = spec.get_resource_manager(GraphEntityLink)
    for r in lrm.list_resources((QB["state"] == "pending").build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        if (link.entity_id, link.proposed_from) != (entity_id, proposed_from):
            continue
        lrm.update(
            r.info.resource_id,  # ty: ignore[unresolved-attribute]
            msgspec.structs.replace(link, state="rejected", evidence=by),
        )


def entity_page(spec: SpecStar, entity_id: str, *, as_user: str) -> EntityPage:
    """One identity, and everything the corpus said about it THAT THIS READER MAY
    SEE.

    What the whole slice was for: a thing, every document that mentioned it under
    whatever name, how often, and on which slide — assembled from the links rather
    than from anything stored twice.

    The filtering is not done here. Every read runs inside
    ``using(as_user, apply_access_scope=True)``, which is the same context
    specstar's own generated routes enter, so each model's registered scope does
    its one job: the entity disappears when no evidence is readable (a bare name
    can leak), and a mention from a collection this reader cannot open never
    arrives. Re-implementing those rules here would be a second copy to keep in
    step — and a permission rule that drifts is a leak.
    """
    erm = spec.get_resource_manager(GraphEntity)
    lrm = spec.get_resource_manager(GraphEntityLink)
    mrm = spec.get_resource_manager(GraphMention)
    with (
        erm.using(as_user, apply_access_scope=True),  # ty: ignore[unknown-argument]
        lrm.using(as_user, apply_access_scope=True),  # ty: ignore[unknown-argument]
        mrm.using(as_user, apply_access_scope=True),  # ty: ignore[unknown-argument]
    ):
        entity = erm.get(entity_id).data  # 404s when nothing vouches for it
        assert isinstance(entity, GraphEntity)
        links: list[GraphEntityLink] = []
        mentions: list[GraphMention] = []
        for r in lrm.list_resources((QB["entity_id"] == entity_id).build()):
            link = r.data
            assert isinstance(link, GraphEntityLink)
            if link.state != "active":
                continue
            try:
                mention = mrm.get(link.mention_id).data
            except ResourceIDNotFoundError:
                continue  # evidence in a collection this reader cannot open
            assert isinstance(mention, GraphMention)
            links.append(link)
            mentions.append(mention)
        related = _related(spec, entity, as_user=as_user)
    return EntityPage(
        entity=entity,
        mentions=mentions,
        links=links,
        occurrences=sum(m.occurrences for m in mentions),
        related=related,
    )


def _related(spec: SpecStar, entity: GraphEntity, *, as_user: str) -> list[Related]:
    """The connections this identity takes part in, from its side.

    Matched on the identity's KEYS, not on one name, which is the payoff of the
    vocabulary layer: an English deck stating the connection under "Reflow Oven"
    shows up on the 回焊爐 page, because the ends resolve through the identity
    rather than being compared as strings.

    Reads stay inside the caller's scope, so a connection stated in a document
    they cannot open never appears — it repeats that document's sentence.
    """
    if not entity.norm_keys:
        return []
    rrm = spec.get_resource_manager(GraphRelationship)
    erm = spec.get_resource_manager(GraphEntity)
    keys = list(entity.norm_keys)
    out: list[Related] = []
    with (
        rrm.using(as_user, apply_access_scope=True),  # ty: ignore[unknown-argument]
        erm.using(as_user, apply_access_scope=True),  # ty: ignore[unknown-argument]
    ):
        for field, direction, far in (
            ("norm_subject", "out", "object"),
            ("norm_object", "in", "subject"),
        ):
            for r in rrm.list_resources((QB[field].in_(keys)).build()):
                rel = r.data
                assert isinstance(rel, GraphRelationship)
                far_surface = getattr(rel, far)
                far_key = getattr(rel, f"norm_{far}")
                far_id, far_name = _identity_of(erm, far_key, far_surface)
                # The predicate resolves through the vocabulary too, so a page
                # shows one connection-word once the two spellings are joined.
                _, predicate = _identity_of(erm, rel.norm_predicate, rel.predicate)
                out.append(
                    Related(
                        direction=direction,
                        predicate=predicate,
                        other_name=far_name,
                        other_entity_id=far_id,
                        quote=rel.quote,
                        source_doc_id=rel.source_doc_id,
                        chunk_id=rel.chunk_id,
                    )
                )
    return out


def _identity_of(erm, key: str, fallback: str) -> tuple[str, str]:
    """The identity a surface resolves to, or the surface itself when the
    vocabulary has not reached it — a connection to something unnamed is still
    worth showing, and pretending otherwise would hide half the graph."""
    for r in erm.list_resources((QB["norm_keys"].contains(key)).build()):
        data = r.data
        assert isinstance(data, GraphEntity)
        return r.info.resource_id, data.canonical_name
    return "", fallback
