"""#309 — the agent-tool authorization funnel.

Every tool listed in ``TOOL_VERBS`` is gated here BEFORE it touches the
workspace: the AI acts as ``Actor.ai(ceiling ∩ speaker, groups included)``, so it
can only do what the current speaker may do on the item — a prompt-injected model
can at worst exercise the speaker's own grants, never exceed them, and never
``use_terminal`` / ``change_permission`` (hard-barred in ``authorize`` whatever
the ceiling). The verb ceiling is DERIVED from the preset's tool allow-list — a
tool the preset grants implies its verb — so there's no second config surface to
drift. See ``docs/plan-permissions.md`` (#309).

``TOOL_VERBS`` IS THE SCOPE, and it is not yet every tool that touches an item.
``save_workflow``, ``save_skill``, ``read_skill``, ``update_todos`` and the
entity tools still reach the workspace without passing here, as does every
tool-package command (``tooling/registry.py`` runs code in the item's sandbox).

The sentence above names the TABLE rather than a category because claiming the
category is exactly what let gaps live: ``list_files`` and ``exists`` were listed
below and gated nowhere, and the enumeration that replaced that claim missed
``infer_modules`` — which creates, deletes and recreates a file at a path the
model chooses. Both are now in the table.
``test_every_tool_that_declares_a_verb_actually_checks_it`` fails on any entry
that drifts back out; nothing yet fails on a tool that never joins.

Closing the rest needs a ceiling that can express a package command's verb, and
one that knows about tools ``build_tools`` grants outside ``allowed_tools`` (it
appends ``read_skill`` itself).
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
    # It writes `.agent/<name>/AGENT.md` into the item's workspace, and that file
    # is a SYSTEM PROMPT every later turn loads and any collaborator's
    # `run_agent` executes. The chat entry gate is `converse`, so leaving it
    # ungated let anyone who could talk to an item leave a standing instruction
    # in it — a worse version of the `save_skill` hole. Granted by `rca`, `pm`
    # and `playground`; not by `_template` or `topic-hub`.
    "save_subagent": "edit_content",
    # It reads the file named by `path` and CREATES-then-DELETES-then-CREATES the
    # one named by `out`, both model-chosen. `edit_content` rather than a pair of
    # checks: its only output channel IS that file, so a speaker who may not write
    # learns nothing from it, and asking for two verbs would refuse a preset that
    # grants this tool and no other reader (#537).
    "infer_modules": "edit_content",
}


# Legacy tool names in a *stored* ``allowed_tools`` list, mapped to their current
# name. Input normalisation only — the old name is NOT a callable alias (the
# model still calls the tool by its registered name); it keeps old config data
# working. #241: ``ls`` was renamed to ``list_files``.
#
# It lives HERE, beside the ceiling that has to apply it, and the tool layer
# imports it, because two readers of one list must not disagree: ``build_tools``
# renamed before REGISTERING and this ceiling did not, so a config saying ``ls``
# would get a working ``list_files`` that this funnel refuses on every call —
# the #537 shape, where a tool that can only say no reads as "stop trying".
#
# DEFENCE IN DEPTH, not a reported defect: on an item turn ``allowed_tools``
# always comes from ``AppCatalog.resolve``, which iterates the App manifest's
# own ``tools``, and no shipped ``app.json`` names a legacy tool — so a stored
# ``ls`` cannot reach here today. It is applied where a reader can be shown to
# disagree with registration, and NOT pushed into ceilings derived from authored
# manifest files, where it would change a clamp and a validator with nothing
# able to exercise either.
LEGACY_TOOL_RENAMES: dict[str, str] = {"ls": "list_files"}


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
    created_by = rm.get_meta(context.investigation_id).created_by
    actor = Actor.ai(context.acting_user, ceiling=ceiling)
    if authorize(actor, verb, item.permission, created_by=created_by):
        return None
    # Only now ask who the speaker's groups are, and only when they could
    # matter. `authorize` is MONOTONE in `actor.groups` — steps 1-4 never read
    # them and `_granted` only unions more subjects — so a refusal is the one
    # state in which they can change the answer, and a refusal by the CEILING
    # (step 3) is not even that: no membership lifts it. Asking here rather than
    # up front keeps the query off every public / owner / direct-grant call, and
    # shrinks the window in which a revoked membership is still believed to the
    # turns that actually lean on a group grant.
    #
    # They have to be here at all because `Actor.ai` was built without them
    # while every human path passes `groups_of(spec, user)`: a verb granted to
    # `group:<id>` was reachable by the person and refused to the agent they
    # were driving, so "ceiling ∩ speaker" was really "ceiling ∩ speaker minus
    # their groups" — not the guarantee this module documents.
    if verb in ceiling:
        actor = Actor.ai(context.acting_user, ceiling=ceiling, groups=context.speaker_groups())
        if authorize(actor, verb, item.permission, created_by=created_by):
            return None
    # The refusal used to be silent. `authorize` logs the CEILING case — that
    # one is a misconfiguration — and logged nothing for the grant case, which
    # is the one that turns on data an operator can go and look at, so a
    # deployment's only evidence was the sentence in a chat bubble. `verb in
    # ceiling` says WHICH of the two this was without a second guess.
    #
    # It names the item and stops: the grant lists are a roster of user ids (an
    # address list under SSO), and this fires on a path any speaker can trigger
    # as often as the model retries.
    logger.warning(
        "authorize_tool: %s denied for user %s on %s item %s "
        "(owner=%s, visibility=%s, speaker groups=%s, in ai ceiling=%s)",
        verb,
        context.acting_user,  # the speaker, not an actor that gets rebound below
        context.app_slug,
        context.investigation_id,
        created_by,
        # `permission is None` ≡ public, which DOES reach a refusal — through the
        # ceiling, on an item nobody has restricted.
        "public" if item.permission is None else item.permission.visibility,
        # `n/a` unless the memberships were actually looked up. A count read off
        # an actor built without them says "this user is in no groups" — the one
        # wrong conclusion for the symptom this line exists to diagnose — and
        # `verb in ceiling` was a PROXY for "did we look", which the empty-speaker
        # path walked straight past. The context is what knows.
        len(actor.groups) if context.speaker_groups_resolved else "n/a",
        verb in ceiling,
    )
    # One sentence, because the code checked one thing. The ceiling case wanted
    # to say "this is a setting, not your permissions" — but the ceiling test
    # short-circuits BEFORE any grant is evaluated, so it cannot know the grants
    # would have allowed it, and a tool absent from the App's `agent.tools` is
    # not in the item's tool settings for anyone to switch on. The distinction
    # is real and belongs in the log above, where it is stated as what was
    # measured rather than as advice.
    return f"error: you don't have permission to {verb.replace('_', ' ')} in this workspace."
