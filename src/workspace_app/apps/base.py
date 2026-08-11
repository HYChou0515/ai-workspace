"""``WorkItemBase`` — the shared base every App's item Struct inherits (#89).

Field tiers (see ``CONTEXT.md`` → "Apps & work items"):

- **Tier 1** — platform-structural, concrete + required: ``title`` / ``owner``
  (+ the universal ``profile``). Every App has them; not opt-out-able.
- **Tier 2** — platform opt-in features typed ``T | UnsetType`` (default
  ``UNSET``). An App **opts in** by *redeclaring* the field as the concrete ``T``
  in its subclass (e.g. ``members: list[str] = []``); leaving it ``UNSET`` means
  the App doesn't have that feature, and msgspec omits it on the wire.
- **Tier 3** — the App's own typed domain fields, added on the subclass
  (RCA: ``severity`` / ``status`` / ``product``).
"""

from __future__ import annotations

from msgspec import UNSET, Struct, UnsetType, field
from specstar.types import IndexableField

from ..perm.model import Permission

# The type each App's `model.py` annotates its `INDEXED_FIELDS` with — matches
# `SpecStar.add_model(indexed_fields=...)`. Declared here (not `list[str]`) so a
# plain string list stays assignable through `list`'s invariance.
IndexedFields = list[str | tuple[str, type] | IndexableField]


class WorkItemBase(Struct):
    title: str
    """Tier 1 — the item headline. Required."""

    owner: str
    """Tier 1 — creator user id (from auth). Required."""

    description: str = ""
    """Tier 1 — multi-line free text (decision 12). Optional; "" when unset."""

    profile: str = "default"
    """Which profile this item was seeded from (starter-content bundle)."""

    attached_preset: str = ""
    """Which picker preset drives this item's turns (#89 decision 23). "" → the
    AppCatalog falls back to the profile's default / first allowed preset."""

    attached_tool_prefs: dict[str, bool] = field(default_factory=dict)
    """Tier 1 — per-item tri-state tool override (#322), sibling of
    ``attached_preset``. Each entry pins one App-ceiling tool ON (``True``) or OFF
    (``False``); an absent key follows the profile/App default (so future
    default changes still flow through). Empty (the default) → every tool follows
    the default. The override ceiling is the App's ``tools``, not the profile.
    Resolved by ``AppCatalog.resolve(tool_prefs=...)``; edited in the web tool
    picker."""

    attached_skill_prefs: dict[str, bool] = field(default_factory=dict)
    """Tier 1 — per-item tri-state *skill* override (#380), the skill sibling of
    ``attached_tool_prefs``. Each entry pins one skill ON (``True``) or OFF
    (``False``); an absent key follows the profile/App default. The override
    ceiling is the App's declared shared skills + the profile's package skills, so
    a force-ON can re-add an available-but-default-off skill. Empty (the default)
    → every skill follows its default. Resolved by
    ``AppCatalog.resolve(skill_prefs=...)``; edited in the web skills picker."""

    env_vars: dict[str, str] = field(default_factory=dict)
    """Tier 1 — environment variables the user sets for THIS item, handed to the
    tools its agent runs. Sibling of ``attached_tool_prefs`` / ``attached_skill_prefs``
    in every respect: per-item configuration, declared once here so an App does
    not opt in (any App can run tools, and any tool may want a key).

    A ``dict`` rather than a list of pairs, for the same reason as
    ``attached_tool_prefs``: a name cannot appear twice, so the shape itself
    rules out the collision. Order is NOT part of the value — the store
    canonicalises key order (its content hash needs a stable one), so the panel
    reads them back sorted rather than in the order they were typed. That is
    fine here and worth knowing: nothing may depend on the order, and a UI that
    promised to preserve it would be promising something the store undoes.

    This is the SOURCE OF TRUTH, and the ONLY copy (#673). The values do not
    land anywhere: they are named on the ``exec`` that dispatches a tool and
    nowhere else (``tooling.registry._exec_tool``), read fresh per turn, so the
    agent's own ``exec`` / ``python`` has nothing to inherit and no file to open.
    An earlier design (#664) wrote them to a file in the sandbox's infra area;
    it was removed because tool and agent share a uid, which made that file
    readable by both or by neither.

    ACCESS IS ASYMMETRIC, and the asymmetry is the point to know:

    * WRITING is ``write_meta`` — this field rides the item PATCH like every
      other, so a Participant cannot set it (the FE withholds the panel to match,
      `useItemAccess.canWriteMeta`).
    * READING is ``read_meta`` — the values are a plain field on the item record
      and are NOT redacted on the way out, so anyone who can open the item can
      read them, and the panel shows them unmasked.
    * USING is ``converse`` — injection does not go through the UI at all, so a
      member who may not edit them still gets them in their turns.

    Treat them as shared-with-the-item, not shared-with-the-owner: an API key put
    here is visible to everyone the item is shared with."""

    external_refs: list[str] = field(default_factory=list)
    """Tier 1 (#700) — the external records this item has already absorbed, as
    opaque ``<system>:<record-id>`` strings (e.g. ``"legacy-rca:12345"``).
    Declared here rather than opted into, for the same reason as ``env_vars``:
    any App can be handed work by an outside system, so this is a platform
    capability, not one App's domain field.

    It exists to answer ONE question — "has this item already taken this
    record?" — which is what stops a legacy site's button from sprawling one
    real-world problem across N items, each holding a fraction of the context.
    Several external records may converge onto one item (the whole point: the
    outside system splits a problem into pieces that only a human can regroup),
    and the SAME record may legitimately appear on several items.

    "At most once per item" is the CALLER'S contract, not a guarantee this field
    makes. Nothing here rejects a duplicate — a create carrying the same ref
    twice stores it twice, and so does a repeated RFC 6902 ``add``. The platform
    is an opaque store for these strings; the recording procedure in
    ``docs/external-handoff.md`` gets the property by re-reading before it
    writes. Anything relying on uniqueness must not assume this field enforces it.

    The value is opaque: it is compared, never parsed. The platform assigns no
    meaning to the ``<system>`` half beyond "the caller says these came from
    different places".

    **NOTHING MAY QUERY THIS FIELD**, and the reason is not the one you might
    expect from ``CLAUDE.md``'s ``.contains`` note — that trap needs a field to
    BE indexed while having lost its ``list[...]`` annotation, which is the
    opposite precondition. Measured here instead: because the field is absent
    from every ``INDEXED_FIELDS`` there is no ``indexed_data`` entry for the
    predicate to match, so a filter on it returns **zero rows** — identically on
    the in-memory and SQL backends. specstar does not swallow it in silence:
    ``_validate_query_fields`` warns that the condition "matches nothing, so the
    query will under-return" (and a deployment may set ``on_unindexed_query`` to
    ``error`` to make it raise). But a warning in a server log is not a guard,
    which is why the ban is enforced by tests.

    Zero rows is the dangerous answer for the question this field exists to
    settle. "Which items already absorbed record X?" answered with nothing reads
    as "none of them", so the caller hands the analysis over again and creates
    the duplicate item this whole design exists to prevent — with a warning
    nobody reads in production as the only trace.

    Callers therefore fetch a page of items and filter the records they already
    hold, which costs zero extra round trips because a listed record carries its
    own ``external_refs``. Two guards keep it that way: no App may index the
    field, and no module here may mention it outside this definition.

    (Indexing it would in fact make a query CORRECT — the annotation is
    ``list[str]``, so specstar registers a list field and ``.contains`` becomes
    exact membership. It stays un-indexed because there is no server-side query
    to justify the write-time cost, not because indexing is unsafe. If a real
    query need ever appears, index it deliberately and delete these guards —
    do not smuggle a filter past them.)"""

    permission: Permission | None = None
    """Tier 1 — access control (#306). The SAME embedded ``Permission`` that
    governs collections / KbChat: ``visibility`` decides whether the per-verb grant
    lists are enforced, ``owner`` is the resource's ``created_by`` (not the
    ``owner`` field, which is a display/notify collaborator — kept SEPARATE, #306
    grill Q4=B). Absent ≡ ``public`` (legacy items, no migration). Set via the
    per-item permission endpoint; enforced by the App WorkItem's access_scope +
    write checker (``apps.registry`` / ``perm``)."""

    members: list[str] | UnsetType = UNSET
    """Tier 2 (opt-in) — collaborators. Redeclare as ``list[str]`` in the App's
    subclass to enable; left ``UNSET`` the App has no members concept."""

    topics: list[str] | UnsetType = UNSET
    """Tier 2 (opt-in) — free-form tags for sidebar grouping. Same opt-in rule."""
