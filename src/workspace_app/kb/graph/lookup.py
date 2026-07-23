"""#628 — the dossier lookup behind the KB agent's ``lookup_entity`` tool.

"What is X" against a slide-heavy corpus is not answered by a definition
sentence — slides rarely contain one. It is answered by ASSEMBLY: the thing's
name in every spelling the documents used, what documents state about it, what
OTHER things have it as a value (#630 — asking about a recipe tells you which
machines run it), every document that mentions it, and what it connects to,
each line carrying the slide it came from. All of that already hangs off :func:`review.entity_page`;
this module resolves a free-typed name to an entity and renders that page as a
plain-text card an agent can quote from.

Deterministic and LLM-free on purpose — the same reasons as
``lookup_glossary``: an exact-key lookup costs nothing, so granting it never
competes with the search budget (#537), and a card that is merely ASSEMBLED
cannot hallucinate. Reads AS THE CALLER: an identity nothing readable vouches
for is "not found" (a bare name can leak a customer code or an unreleased
part), and the did-you-mean list is filtered the same way — a hint is a read.
"""

from __future__ import annotations

from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources.graph import GraphEntity
from .normalize import display_value, norm_surface
from .review import EntityPage, entity_page

# A did-you-mean longer than this stops helping the agent pick and starts
# being a directory listing.
_MAX_CANDIDATES = 5


def entity_card(spec: SpecStar, name: str, *, as_user: str) -> str:
    """The plain-text dossier for ``name``, or a not-found note with near
    misses the caller may actually open."""
    key = norm_surface(name)
    erm = spec.get_resource_manager(GraphEntity)
    for r in erm.list_resources(QB["norm_keys"].contains(key).build()):
        entity = r.data
        assert isinstance(entity, GraphEntity)
        if entity.merged_into:
            continue  # a tombstone; its evidence lives at the host now
        try:
            page = entity_page(spec, r.info.resource_id, as_user=as_user)  # ty: ignore[unresolved-attribute]
        except ResourceIDNotFoundError:
            break  # unreadable — same face as unknown
        return _render(spec, page, as_user=as_user)
    return _not_found(spec, name, key, as_user=as_user)


def _render(spec: SpecStar, page: EntityPage, *, as_user: str) -> str:
    entity = page.entity
    kind = ""
    if entity.kind_id:
        try:
            kind = entity_page(spec, entity.kind_id, as_user=as_user).entity.canonical_name
        except ResourceIDNotFoundError:
            kind = ""  # the kind rests on evidence this caller cannot read
    head = entity.canonical_name if not kind else f"{entity.canonical_name} ({kind})"
    lines = [f"# {head}"]
    # The spellings documents actually used — never the normalised keys.
    others = sorted({m.surface for m in page.mentions} - {entity.canonical_name})
    if others:
        lines.append("Also written: " + ", ".join(others))
    docs = sorted({m.source_doc_id for m in page.mentions})
    lines.append(f"Mentioned {page.occurrences} times across {len(docs)} documents.")

    if page.claims:
        lines += ["", "## What the documents state about it"]
        for c in page.claims:
            period = f" ({c.period})" if c.period else ""
            shown = display_value(c.value, c.unit)
            lines.append(f"- {c.attribute} = {shown}{period} [{c.source_doc_id}]")

    if page.value_of:
        # #630 P5: the statement table read from the far end — what runs this
        # recipe, what is made of this material, who reports to this manager.
        lines += ["", "## Things that have it as a value"]
        for c in page.value_of:
            period = f" ({c.period})" if c.period else ""
            lines.append(f"- {c.subject} — {c.attribute}{period} [{c.source_doc_id}]")

    lines += ["", "## Documents that mention it"]
    for m in page.mentions:
        lines.append(f"- {m.source_doc_id} — as 「{m.surface}」 ×{m.occurrences}")

    if page.related:
        lines += ["", "## Connections"]
        for rel in page.related:
            arrow = (
                f"{entity.canonical_name} —{rel.predicate}→ {rel.other_name}"
                if rel.direction == "out"
                else f"{rel.other_name} —{rel.predicate}→ {entity.canonical_name}"
            )
            quote = f" — 「{rel.quote}」" if rel.quote else ""
            lines.append(f"- {arrow}{quote} [{rel.source_doc_id}]")
    return "\n".join(lines)


def _not_found(spec: SpecStar, name: str, key: str, *, as_user: str) -> str:
    note = f"Entity not found: 「{name}」."
    hints = _candidates(spec, key, as_user=as_user)
    if hints:
        return note + " Closest known names: " + ", ".join(hints) + ". Re-ask with one exact name."
    return note


def _close(key: str, entity_keys: list[str]) -> bool:
    """Near-miss = shares a whitespace token, or one contains the other.

    Both are spelling-level relations — no meaning is guessed. Two characters
    is the floor: a single shared CJK character would relate almost everything
    to almost everything.
    """
    tokens = {t for t in key.split() if len(t) >= 2}
    for other in entity_keys:
        if len(other) >= 2 and (other in key or key in other):
            return True
        for tok in other.split():
            if len(tok) >= 2 and (tok in tokens or tok in key):
                return True
    return False


def _candidates(spec: SpecStar, key: str, *, as_user: str) -> list[str]:
    """Nearby names the caller may actually open — a hint is a read, so every
    candidate is verified with the same scoped page read before it is named."""
    if len(key) < 2:
        return []
    erm = spec.get_resource_manager(GraphEntity)
    out: list[str] = []
    for r in erm.list_resources(QB.all().build()):
        entity = r.data
        assert isinstance(entity, GraphEntity)
        if entity.merged_into or not entity.collection_ids:
            continue
        if not _close(key, entity.norm_keys):
            continue
        try:
            entity_page(spec, r.info.resource_id, as_user=as_user)  # ty: ignore[unresolved-attribute]
        except ResourceIDNotFoundError:
            continue  # unreadable — must not be hinted either
        out.append(entity.canonical_name)
        if len(out) >= _MAX_CANDIDATES:
            break
    return out
