"""#534 B — deciding the proposals, and reading the vocabulary back.

The review queue is the only place this system asks a person for attention, so
everything here is about not wasting it. A decision is remembered, so a question
already answered is never asked again — re-proposing a rejected pair would
re-spend the model AND put the same question back every week, which is exactly
how a queue stops being read.
"""

from __future__ import annotations

from dataclasses import dataclass

import msgspec
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources.graph import GraphEntity, GraphEntityLink, GraphMention


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


@dataclass(frozen=True)
class EntityPage:
    """One identity and every piece of evidence for it, across documents."""

    entity: GraphEntity
    mentions: list[GraphMention]
    links: list[GraphEntityLink]
    occurrences: int


def list_proposals(spec: SpecStar) -> list[Proposal]:
    """Every merge waiting on a person, one row per pair rather than per mention.

    A proposal is stored as pending links (so accepting it is the same absorption
    every other basis performs), but a reviewer is answering ONE question about
    two identities, and showing them the same question once per mention would bury
    it.
    """
    lrm = spec.get_resource_manager(GraphEntityLink)
    erm = spec.get_resource_manager(GraphEntity)
    seen: set[tuple[str, str]] = set()
    out: list[Proposal] = []
    for r in lrm.list_resources((QB["state"] == "pending").build()):
        link = r.data
        assert isinstance(link, GraphEntityLink)
        pair = (link.entity_id, link.proposed_from)
        if not link.proposed_from or pair in seen:
            continue
        seen.add(pair)
        host = erm.get(link.entity_id).data
        other = erm.get(link.proposed_from).data
        assert isinstance(host, GraphEntity) and isinstance(other, GraphEntity)
        out.append(
            Proposal(
                entity_id=link.entity_id,
                proposed_from=link.proposed_from,
                name=host.canonical_name,
                other_name=other.canonical_name,
                why=link.evidence,
            )
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
    return EntityPage(
        entity=entity,
        mentions=mentions,
        links=links,
        occurrences=sum(m.occurrences for m in mentions),
    )
