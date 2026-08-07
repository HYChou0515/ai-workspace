"""Context-card routes (#106) — the authoring + lookup surface for the
lightweight glossary path.

Writes go through specstar **custom actions** (`create_action` / `update_action`)
so `norm_keys` is derived from `keys` in the SAME write — the FE never sends
`norm_keys`, and there is no event handler / write-back loop. The actions are
registered on the spec *before* `spec.apply(app)`; the read routes are plain
FastAPI routes added after.

NOTE: no ``from __future__ import annotations`` here — specstar's custom-action
route builder introspects the handler signature at apply() time and can't resolve
stringised ForwardRef body types, so the annotations must be real classes.
"""

from fastapi import APIRouter, Body, FastAPI
from pydantic import BaseModel
from specstar import SpecStar

from ..kb.context_cards import derive_norm_keys, effective_keys, lookup
from ..resources.kb import ContextCard


class ContextCardBody(BaseModel):
    """Author input for a new card. `norm_keys` is intentionally absent — it is
    server-derived from `keys`."""

    collection_id: str
    keys: list[str]
    title: str = ""
    body: str = ""
    reference_doc_ids: list[str] = []
    """#518: the documents that back this card. Optional — omitted ⇒ no links, which
    is today's behaviour."""


class ContextCardPatchBody(BaseModel):
    """Edit input. `collection_id` is immutable (a card stays in its collection);
    `norm_keys` is re-derived server-side from the new `keys`."""

    keys: list[str]
    title: str = ""
    body: str = ""
    reference_doc_ids: list[str] | None = None
    """#518: tri-state, unlike the create body. ``None`` (the default, and what every
    client that predates the field sends) ⇒ KEEP the card's existing links; a list ⇒
    replace them (``[]`` clears). An edit form that doesn't render links must not
    silently discard them just by saving."""


def register_context_card_actions(spec: SpecStar) -> None:
    """Register the create/update custom actions. MUST be called before
    `spec.apply(app)` (the actions materialise into routes at apply time)."""

    @spec.create_action("context-card", path="author", label="Add context card")
    def author_context_card(body: ContextCardBody = Body(...)) -> ContextCard:  # noqa: B008
        # #701: the shared definition of what a card is keyed under — the title
        # stands in when no key normalises, or the card has no `norm_keys` and could
        # never be found by lookup / match. This route used to restate it and the two
        # disagreed on whitespace, which is a difference a person sees: `keys` is what
        # the editor displays.
        keys = effective_keys(body.keys, body.title)
        return ContextCard(
            collection_id=body.collection_id,
            keys=keys,
            norm_keys=derive_norm_keys(keys),
            title=body.title,
            body=body.body,
            reference_doc_ids=body.reference_doc_ids,
        )

    @spec.update_action("context-card", path="edit", label="Edit context card", mode="update")
    def edit_context_card(
        existing: ContextCard,
        body: ContextCardPatchBody = Body(...),  # noqa: B008
    ) -> ContextCard:
        # #701: the same fallback as `author` / `create_context_card` /
        # `update_context_card`. Without it, clearing the last term left the card with
        # empty `norm_keys` — still listed, still editable, and reachable by no lookup,
        # match or upsert ever again.
        keys = effective_keys(body.keys, body.title)
        return ContextCard(
            collection_id=existing.collection_id,  # immutable across edits
            keys=keys,
            norm_keys=derive_norm_keys(keys),
            title=body.title,
            body=body.body,
            # #518: absent ⇒ carry the existing links forward (see the body's docstring).
            reference_doc_ids=(
                existing.reference_doc_ids
                if body.reference_doc_ids is None
                else body.reference_doc_ids
            ),
        )


class LookupBody(BaseModel):
    """Batch lookup input — the terms an external caller's LLM extracted."""

    terms: list[str]


class CardOut(BaseModel):
    """A card as returned to an external caller: the content its LLM stuffs into
    its own context. No `norm_keys` (an internal detail) and no resource id."""

    keys: list[str]
    title: str
    body: str


class LookupOut(BaseModel):
    """`results[term]` = the cards matching that ORIGINAL input term (empty list
    on a miss). Same deterministic primitive as the internal pre-scan."""

    results: dict[str, list[CardOut]]


def register_context_card_routes(app: FastAPI | APIRouter, spec: SpecStar) -> None:
    """Plain read routes (added AFTER `spec.apply`). The exposed `get(term)`:
    deterministic, exact, batch, scoped to one collection."""

    @app.post("/kb/collections/{collection_id}/context-cards/lookup")
    def lookup_context_cards(collection_id: str, body: LookupBody) -> LookupOut:
        hits = lookup(spec, collection_id, body.terms)
        return LookupOut(
            results={
                term: [CardOut(keys=c.keys, title=c.title, body=c.body) for c in cards]
                for term, cards in hits.items()
            }
        )
