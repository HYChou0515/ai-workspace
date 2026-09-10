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
``mention_user`` is outside it too and is the one that does not look like it:
it writes a Notification carrying the item's id AND TITLE to arbitrary user ids
— and a ``role="mention"`` message into the item's own chat — so it discloses a
name to people who may not read the item, on nothing more than the ``converse``
entry gate. So is ``search_wiki``, whose ``scopes`` fall back to the ITEM id
when a turn has no collections, making it a full-text grep of the workspace
that returns ``path:line: text``; no shipped App grants it, which is a
convention with nothing enforcing it.

That list is now what it is: an enumeration that has been short three times
(``list_files``/``exists``, then ``infer_modules``, then these two). Closing it
is its own change — it needs a ceiling that can express a package command's
verb, and one that knows about tools ``build_tools`` grants outside
``allowed_tools`` — and until that lands, a tool ABSENT from this table has not
been judged safe; it has not been judged.

Two things the table cannot express, handled elsewhere rather than by widening
a row:

* A verb a tool exercises only on ONE BRANCH. ``write_file`` / ``edit_file``
  hand back the current content when the write is rejected, which is a read —
  but demanding ``read_content`` up front would refuse an add-only collaborator
  every ordinary write. The echo itself is gated instead (``_conflict_echo``),
  so the rejection still explains itself and stops short of the contents.
* An existence oracle that is INHERENT to the verb, where the probe COSTS
  something. ``delete_file`` answers "not found" differently from "deleted",
  and no gate hides that from somebody who may delete — but each probe destroys
  the file it asks about. ``write_file`` is the same shape ("already exists" vs
  "wrote N bytes") and free, which is why it keeps ``edit_content`` alone: it
  cannot address an existing file beyond learning that it is there.

  ``edit_file`` was in this list and should not have been. Its probe is free AND
  repeatable AND composable — see its row in the table — so it is gated on
  ``read_content`` instead of accepted. "One bit per call" is not a bound when
  the caller is an agent.

Not accepted, and not this module's to fix: ``files/facade.py``'s ``edit``
round-trips through ``decode(errors="replace")``, so an edit against a file that
is not valid UTF-8 rewrites every invalid byte as U+FFFD and reports success. A
20-byte PNG came back 32 bytes with its signature gone. Gating ``edit_file`` on
``read_content`` narrows who can do it; it does not stop the owner doing it by
accident.

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

# The permission verbs each item-level tool exercises — ALL of them, because a
# tool that reads and writes needs both checked and both in the ceiling. A tool
# absent here is not an item-verb tool: kb tools (their cross-collection read is
# checked per #305), `lookup_user` (a read-only directory), wiki/skill (their own
# contexts). `mention_user` is absent and should not be read as safe: it WRITES a
# Notification carrying the item's id and title to arbitrary user ids, on the
# `converse` entry gate — see the module docstring.
TOOL_VERBS: dict[str, tuple[Verb, ...]] = {
    "read_file": ("read_content",),
    "read_image": ("read_content",),
    "show_file": ("read_content",),
    "list_files": ("read_content",),
    "exists": ("read_content",),
    "write_file": ("edit_content",),
    # BOTH, and not because of the echo: `old_string` must match the current file
    # EXACTLY AND UNIQUELY, so every successful edit is a statement about content
    # the caller had to have read. `edit_file(path, X, X)` replaces X with itself
    # — the file is unchanged and the answer is one bit, "does X occur exactly
    # once" — and an agent is precisely the automation that walks that bit into
    # the whole file: 28 calls recovered a secret digit by digit in review.
    # `write_file` stays single-verb: an add-only collaborator creating new files
    # is a real shape, and it cannot address an existing file at all.
    "edit_file": ("read_content", "edit_content"),
    "delete_file": ("edit_content",),
    # `execute` is NOT decomposed, and that is the one deliberate exception to the
    # rule above: the verb means "run arbitrary commands in this workspace", which
    # is what a shell IS. A speaker who may run commands but may not read files is
    # a state no gate can hold — the first command reads whatever it likes — so
    # splitting it would describe an enforcement that does not exist.
    "exec": ("execute",),
    # `make_deck` is NOT that. It grants no shell: it reads the source files the
    # model names, writes the deck where the model says, and lists directories,
    # through callbacks handed to `run_make_deck`. Gated on `execute` alone, a
    # speaker holding `converse` + `execute` and neither content verb could read
    # any file and write any other — and unlike `infer_modules` this one is
    # granted by `rca` and `playground` today.
    "make_deck": ("read_content", "edit_content", "execute"),
    # It writes `.agent/<name>/AGENT.md` into the item's workspace, and that file
    # is a SYSTEM PROMPT every later turn loads and any collaborator's
    # `run_agent` executes. The chat entry gate is `converse`, so leaving it
    # ungated let anyone who could talk to an item leave a standing instruction
    # in it — a worse version of the `save_skill` hole. Granted by `rca`, `pm`
    # and `playground`; not by `_template` or `topic-hub`.
    "save_subagent": ("edit_content",),
    # BOTH, and the first version of this entry had only `edit_content` on the
    # argument that "its only output channel is the file it writes". That was the
    # wrong question. `_read_step_names` treats every non-empty line of a
    # non-matching file as a step, each step is handed to a sub-agent, and
    # `on_exec_output` streams that sub-agent's queries and reasoning back into
    # the parent turn — so `path` is a read channel whatever `out` says. Its two
    # distinct failure messages are also an existence oracle, which is the very
    # thing `exists` is gated for. A preset holding this tool and no reader is
    # exactly the preset that must not be handed a reader for free.
    "infer_modules": ("read_content", "edit_content"),
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
    """The AI's verb ceiling implied by the preset's allowed TOOLS — the union of
    every verb each granted tool exercises. ``None`` ≡ the default workspace
    toolset ⇒ every item verb. ``change_permission`` / ``use_terminal`` are never
    tool verbs, so they can never enter the ceiling (and are hard-barred in
    ``authorize`` regardless)."""
    names = TOOL_VERBS if allowed is None else [LEGACY_TOOL_RENAMES.get(n, n) for n in allowed]
    return frozenset(v for n in names if n in TOOL_VERBS for v in TOOL_VERBS[n])


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
        # Equal to `actor.user_id` on every path today (both actors are built
        # from it); read from the speaker so it stays the speaker if one is ever
        # built for somebody else.
        context.acting_user,
        context.app_slug,
        context.investigation_id,
        created_by,
        # `permission is None` ≡ public, which DOES reach a refusal — through the
        # ceiling, on an item nobody has restricted.
        "public" if item.permission is None else item.permission.visibility,
        # Read off the actor THIS decision used: it carries groups exactly when
        # `verb in ceiling`, so the count is never a number measured on a
        # different object. A memo-backed "were they ever resolved?" is not the
        # same question — it stays true for the rest of the turn, so a later
        # ceiling refusal on the same context reported the earlier call's
        # membership count against an actor that had none.
        #
        # An empty speaker legitimately reports 0: `groups_of` answers that
        # without touching the store, and nobody-behind-the-turn genuinely has no
        # memberships. `n/a` means the question was never put — a backstop today,
        # since a registered built-in's verb is always in the ceiling it was
        # derived from.
        len(actor.groups) if verb in ceiling else "n/a",
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
