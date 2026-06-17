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

from fastapi import Body
from pydantic import BaseModel
from specstar import SpecStar

from ..kb.context_cards import derive_norm_keys
from ..resources.kb import ContextCard


class ContextCardBody(BaseModel):
    """Author input for a new card. `norm_keys` is intentionally absent — it is
    server-derived from `keys`."""

    collection_id: str
    keys: list[str]
    title: str = ""
    body: str = ""


class ContextCardPatchBody(BaseModel):
    """Edit input. `collection_id` is immutable (a card stays in its collection);
    `norm_keys` is re-derived server-side from the new `keys`."""

    keys: list[str]
    title: str = ""
    body: str = ""


def register_context_card_actions(spec: SpecStar) -> None:
    """Register the create/update custom actions. MUST be called before
    `spec.apply(app)` (the actions materialise into routes at apply time)."""

    @spec.create_action("context-card", path="author", label="Add context card")
    def author_context_card(body: ContextCardBody = Body(...)) -> ContextCard:  # noqa: B008
        return ContextCard(
            collection_id=body.collection_id,
            keys=body.keys,
            norm_keys=derive_norm_keys(body.keys),
            title=body.title,
            body=body.body,
        )

    @spec.update_action("context-card", path="edit", label="Edit context card", mode="update")
    def edit_context_card(
        existing: ContextCard,
        body: ContextCardPatchBody = Body(...),  # noqa: B008
    ) -> ContextCard:
        return ContextCard(
            collection_id=existing.collection_id,  # immutable across edits
            keys=body.keys,
            norm_keys=derive_norm_keys(body.keys),
            title=body.title,
            body=body.body,
        )
