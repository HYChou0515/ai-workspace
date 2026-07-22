"""The single decision point for #262: `authorize(actor, verb, permission)`.

Every route and every agent tool funnels through here. The agent and the content
it reads are untrusted — nothing is gated by asking the model nicely; this
function is the gate. See `docs/plan-permissions.md`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .model import (
    AI_FORBIDDEN,
    ALL,
    Permission,
    Subject,
    Verb,
    group_subject,
    user_subject,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Actor:
    """Who is acting. A direct human action has one principal; an AI turn (or a
    background job) carries the human it serves plus the preset's verb ceiling."""

    user_id: str  # the human: a direct user, the current speaker, or a job initiator
    is_ai: bool = False  # True when the AI/automation acts on the human's behalf
    ceiling: frozenset[str] | None = None  # AI verb allow-list; None ⇒ all verbs
    groups: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def human(cls, user_id: str, *, groups: frozenset[str] = frozenset()) -> Actor:
        return cls(user_id=user_id, groups=frozenset(groups))

    @classmethod
    def ai(
        cls,
        user_id: str,
        ceiling: frozenset[str] | None,
        *,
        groups: frozenset[str] = frozenset(),
    ) -> Actor:
        return cls(
            user_id=user_id,
            is_ai=True,
            ceiling=None if ceiling is None else frozenset(ceiling),
            groups=frozenset(groups),
        )

    @property
    def subjects(self) -> frozenset[Subject]:
        """The grant targets that resolve to this actor (the `all` wildcard is
        matched on the grant side, not carried here)."""
        return frozenset({user_subject(self.user_id), *(group_subject(g) for g in self.groups)})


def _effective_grants(permission: Permission, verb: Verb) -> set[Subject]:
    grants = set(permission.grants(verb))
    if verb == "add_content":
        grants |= set(permission.grants("edit_content"))  # edit_content ⊇ add_content
    return grants


def _granted(actor: Actor, permission: Permission, verb: Verb) -> bool:
    grants = _effective_grants(permission, verb)
    return ALL in grants or bool(actor.subjects & grants)


def authorize(
    actor: Actor,
    verb: Verb,
    permission: Permission | None,
    *,
    created_by: str,
    superusers: frozenset[str] = frozenset(),
    discoverable_restricted: bool = False,
) -> bool:
    """Whether `actor` may do `verb` on a resource owned by `created_by`.

    `permission is None` ≡ the resource is `public` (no object configured yet).
    The `read_meta` gate (404 when absent) is sequenced by the CALLER — it checks
    `read_meta` first, then the specific verb — so this function stays per-verb.

    ``discoverable_restricted`` is the caller-declared TIER SEMANTIC for its
    resource family (#605): True means `restricted` itself makes the EXISTENCE
    visible — `read_meta` passes for every insider with no per-subject grant, so
    a fresh restricted resource is discoverable (name/owner visible, disclosure
    eligible) instead of behaving like `private`. Collections declare True (the
    knowledge-sharing surface, where "there IS an answer you can't read" must be
    tellable); resources that use restricted as "private + an invite list" — a
    shared chat, a work item's member set, a per-doc tightening override — keep
    the default False, so sharing with alice never announces existence to carol.
    Content and every other verb stay grant-gated either way; `private` remains
    fully hidden (404) in both modes.
    """
    # 1. Hard bars on the AI — never, whatever the ceiling / owner / superuser.
    if actor.is_ai and verb in AI_FORBIDDEN:
        logger.warning("authorize: ai-forbidden verb %s denied for user %s", verb, actor.user_id)
        return False
    # 2. A direct human superuser bypasses everything (an AI *driven by* one does
    #    not — it stays ceiling ∩ speaker).
    if not actor.is_ai and actor.user_id in superusers:
        logger.debug("authorize: superuser %s bypass -> %s granted", actor.user_id, verb)
        return True
    # 3. The preset ceiling caps which verbs the AI may ever do.
    if actor.is_ai and actor.ceiling is not None and verb not in actor.ceiling:
        logger.warning(
            "authorize: verb %s outside ai ceiling, denied for user %s", verb, actor.user_id
        )
        return False
    # 4. The owner controls their own resource (all verbs, incl. change_permission).
    if actor.user_id == created_by:
        return True
    # 5. Per-resource decision against the acting human identity.
    perm = permission if permission is not None else Permission()
    if verb == "change_permission":
        # Never made public by visibility — only the explicit grant list (plus
        # owner/superuser, already returned above) may rewire access control.
        return _granted(actor, perm, verb)
    if perm.visibility == "public":
        return True
    if perm.visibility == "private":
        return False
    if discoverable_restricted and verb == "read_meta":
        # #605: this family's `restricted` means "every insider may know it
        # exists" — see the docstring. Families that keep the default still
        # need an explicit read_meta grant to be discoverable.
        return True
    return _granted(actor, perm, verb)  # restricted
