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

import logging
from typing import TYPE_CHECKING

from specstar.types import ResourceIDNotFoundError

from ..apps.base import WorkItemBase
from ..perm import Actor, authorize
from ..perm.model import Verb

if TYPE_CHECKING:
    from .context import AgentToolContext

logger = logging.getLogger(__name__)

# The permission verb each item-level tool exercises. A tool absent here is not an
# item-verb tool: kb tools (their cross-collection read is checked per #305),
# mention/lookup (read-only directory), wiki/skill (their own contexts).
TOOL_VERBS: dict[str, Verb] = {
    "read_file": "read_content",
    "read_image": "read_content",
    "show_file": "read_content",
    "list_files": "read_content",
    "exists": "read_content",
    "write_file": "edit_content",
    "edit_file": "edit_content",
    "delete_file": "edit_content",
    "exec": "execute",
    "make_deck": "execute",
}


# Names an older config may still carry. ``build_tools`` maps these before it
# REGISTERS a tool, so the ceiling has to read the same list through the same map
# — a config saying ``ls`` got a working ``list_files`` that this funnel then
# refused on every call, which is the #537 shape: a tool that can only say no
# reads to a model as "stop trying". One definition, imported by the tool layer.
LEGACY_TOOL_RENAMES: dict[str, str] = {"ls": "list_files"}

# What each item verb lets the agent DO, for a refusal a reader can act on. The
# keys are exactly ``TOOL_VERBS``' values; the fallback covers a verb added to
# one and not the other rather than raising in the middle of a refusal.
_VERB_ACTIONS: dict[Verb, str] = {
    "read_content": "read files",
    "edit_content": "change files",
    "execute": "run commands",
}


def ceiling_from_tools(allowed: list[str] | None) -> frozenset[Verb]:
    """The AI's verb ceiling implied by the preset's allowed TOOLS. ``None`` ≡ the
    default workspace toolset ⇒ every item verb. ``change_permission`` /
    ``use_terminal`` are never tool verbs, so they can never enter the ceiling (and
    are hard-barred in ``authorize`` regardless)."""
    if allowed is None:
        return frozenset(TOOL_VERBS.values())
    return frozenset(
        TOOL_VERBS[name] for n in allowed if (name := LEGACY_TOOL_RENAMES.get(n, n)) in TOOL_VERBS
    )


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
    ceiling = ceiling_from_tools(allowed)
    # WITH the speaker's groups. Every human path builds its actor with
    # `groups_of(spec, user)`; this one did not, so a verb granted to
    # `group:<id>` was reachable by the person and refused to the agent they
    # were driving — "ceiling ∩ speaker" was really "ceiling ∩ speaker minus
    # their groups", which is not the guarantee this module documents.
    actor = Actor.ai(context.acting_user, ceiling=ceiling, groups=context.speaker_groups())
    created_by = rm.get_meta(context.investigation_id).created_by
    if authorize(actor, verb, item.permission, created_by=created_by):
        return None
    # Two unrelated causes were sharing one sentence, and the more common of the
    # two logged NOTHING — so a refusal in a deployment could only be guessed at
    # from the chat bubble that carried it. A ceiling refusal is a SETTING (the
    # tool is switched off for this item, and no permission anyone holds would
    # change it); a grant refusal is about this person. Sending the first one to
    # the permission panel wastes the only move the reader has.
    #
    # `AI_FORBIDDEN` verbs cannot arrive here — no tool maps to them
    # (`test_hard_barred_verbs_can_never_enter_a_tool_ceiling`).
    if verb not in ceiling:
        why = "not in this item's resolved tool set"
        message = (
            f"error: this item's agent cannot {_VERB_ACTIONS.get(verb, verb)} — those tools are "
            "switched off in the item's tool settings. This is a setting, not your permissions."
        )
    else:
        why = "the speaker holds no grant for it"
        message = f"error: you don't have permission to {verb.replace('_', ' ')} in this workspace."
    logger.warning(
        "authorize_tool: %s denied on %s item %s for user %s (groups %s) — %s; "
        "tools=%s permission=%r",
        verb,
        context.app_slug,
        context.investigation_id,
        actor.user_id,
        sorted(actor.groups),
        why,
        "default" if allowed is None else sorted(allowed),
        item.permission,
    )
    return message
