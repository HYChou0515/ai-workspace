"""#309 — the agent-tool authorization funnel.

Every item-level agent tool (file ops / exec) is gated here BEFORE it touches the
workspace: the AI acts as ``Actor.ai(ceiling ∩ speaker)``, so it can only do what
the current speaker may do on the item — a prompt-injected model can at worst
exercise the speaker's own grants, never exceed them, and never ``use_terminal`` /
``change_permission`` (hard-barred in ``authorize`` whatever the ceiling). The verb
ceiling is DERIVED from the preset's tool allow-list — a tool the preset grants
implies its verb — so there's no second config surface to drift. See
``docs/plan-permissions.md`` (#309).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from specstar.types import ResourceIDNotFoundError

from ..apps.base import WorkItemBase
from ..perm import Actor, authorize
from ..perm.model import Verb

if TYPE_CHECKING:
    from .context import AgentToolContext

# The permission verb each item-level tool exercises. A tool absent here is not an
# item-verb tool: kb tools (their cross-collection read is checked per #305),
# mention/lookup (read-only directory), wiki/skill (their own contexts).
TOOL_VERBS: dict[str, Verb] = {
    "read_file": "read_content",
    "read_image": "read_content",
    # #513 P3: reads the uploaded image doc's content to classify it.
    "classify_defect": "read_content",
    "list_files": "read_content",
    "exists": "read_content",
    "write_file": "edit_content",
    "edit_file": "edit_content",
    "delete_file": "edit_content",
    "exec": "execute",
    "make_deck": "execute",
}


def ceiling_from_tools(allowed: list[str] | None) -> frozenset[Verb]:
    """The AI's verb ceiling implied by the preset's allowed TOOLS. ``None`` ≡ the
    default workspace toolset ⇒ every item verb. ``change_permission`` /
    ``use_terminal`` are never tool verbs, so they can never enter the ceiling (and
    are hard-barred in ``authorize`` regardless)."""
    if allowed is None:
        return frozenset(TOOL_VERBS.values())
    return frozenset(TOOL_VERBS[n] for n in allowed if n in TOOL_VERBS)


def authorize_tool(context: AgentToolContext, verb: Verb) -> str | None:
    """Gate an item-level tool call. Returns ``None`` when allowed, or a
    model-facing error string when the current speaker lacks ``verb`` on the item.

    A context with no item (a wiki / KB / workflow turn — no ``spec`` + item +
    ``app_slug``) is not item-gated here and returns ``None``: workflow
    continuations ride the entry-gate rule (authorised at the boundary, not
    re-checked per tool), and kb tools carry their own cross-resource check (#305).
    """
    if context.spec is None or not context.investigation_id or not context.app_slug:
        return None
    from ..apps.registry import app_model  # local: keep the apps import lazy

    try:
        model = app_model(context.app_slug)
    except KeyError:
        return None  # a test-synthetic / non-App slug — nothing to authorize against
    rm = context.spec.get_resource_manager(model)
    try:
        item = rm.get(context.investigation_id).data
    except ResourceIDNotFoundError:
        return None  # item gone → let the underlying tool report it
    assert isinstance(item, WorkItemBase)
    allowed = context.agent_config.allowed_tools if context.agent_config is not None else None
    actor = Actor.ai(context.acting_user, ceiling=ceiling_from_tools(allowed))
    created_by = rm.get_meta(context.investigation_id).created_by
    if authorize(actor, verb, item.permission, created_by=created_by):
        return None
    return f"error: you don't have permission to {verb.replace('_', ' ')} in this workspace."
