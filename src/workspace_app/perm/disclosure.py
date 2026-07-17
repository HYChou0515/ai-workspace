"""Permission disclosure — the platform primitive behind "there IS an answer, but
you can't see it, because you lack permission".

The system used to silently drop any resource a caller couldn't read. That hides
the fact that an answer exists at all. `partition_by_disclosure` instead splits
candidate resources into three tiers, so a caller can surface the MIDDLE one:

  - `readable`     — the actor may `read_content`; content may be shown (unchanged).
  - `discoverable` — the actor may `read_meta` but NOT `read_content`; the resource
                     EXISTS to them (its name may be surfaced) but its bytes are
                     withheld. This is the new seam.
  - `hidden`       — not even `read_meta` (private / restricted-ungranted); a uniform
                     404 that is NEVER disclosed.

The disclosure axis is the existing `read_meta` grant, orthogonal to the
public/restricted/private label (a private resource yields no `read_meta` for a
non-owner, so it stays hidden — the 404 is preserved). The primitive is pure — the
caller loads each resource's `Permission` + owner and feeds tuples — so it lives in
`perm/` free of any resource import and generalises to any protected resource
(collections first; docs / chats / work-items reuse it later).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .authorize import Actor, authorize
from .model import Permission


@dataclass(frozen=True)
class DisclosurePartition:
    """Candidate ids split by what the actor may do, input order preserved within
    each tier. Every input id lands in exactly one list."""

    readable: list[str]
    discoverable: list[str]
    hidden: list[str]


def partition_by_disclosure(
    actor: Actor,
    entries: Iterable[tuple[str, Permission | None, str]],
    *,
    superusers: frozenset[str] = frozenset(),
) -> DisclosurePartition:
    """Split ``(id, permission, created_by)`` entries into the three disclosure
    tiers for ``actor``.

    ``read_content`` is checked first, so an actor who may read the bytes is always
    ``readable`` (owner / superuser / public / granted) and never merely
    ``discoverable``. Only when ``read_content`` is denied does ``read_meta`` decide
    between ``discoverable`` (existence known) and ``hidden`` (unknown). Both checks
    funnel through the single ``authorize`` decision point, so this stays a faithful
    mirror of route/tool gating — no parallel policy."""
    readable: list[str] = []
    discoverable: list[str] = []
    hidden: list[str] = []
    for rid, perm, created_by in entries:
        if authorize(actor, "read_content", perm, created_by=created_by, superusers=superusers):
            readable.append(rid)
        elif authorize(actor, "read_meta", perm, created_by=created_by, superusers=superusers):
            discoverable.append(rid)
        else:
            hidden.append(rid)
    return DisclosurePartition(readable=readable, discoverable=discoverable, hidden=hidden)
