"""The single decision point for #262: `authorize(actor, verb, permission)`.

Every route and every agent tool funnels through here. The agent and the content
it reads are untrusted — nothing is gated by asking the model nicely; this
function is the gate. See `docs/plan-permissions.md`.
"""

from __future__ import annotations

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
) -> bool:
    """Whether `actor` may do `verb` on a resource owned by `created_by`.

    `permission is None` ≡ the resource is `public` (no object configured yet).
    The `read_meta` gate (404 when absent) is sequenced by the CALLER — it checks
    `read_meta` first, then the specific verb — so this function stays per-verb.
    """
    # 1. Hard bars on the AI — never, whatever the ceiling / owner / superuser.
    if actor.is_ai and verb in AI_FORBIDDEN:
        return False
    # 2. A direct human superuser bypasses everything (an AI *driven by* one does
    #    not — it stays ceiling ∩ speaker).
    if not actor.is_ai and actor.user_id in superusers:
        return True
    # 3. The preset ceiling caps which verbs the AI may ever do.
    if actor.is_ai and actor.ceiling is not None and verb not in actor.ceiling:
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
    return _granted(actor, perm, verb)  # restricted
