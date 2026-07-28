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

    This is the SOURCE OF TRUTH. The copy the tools actually read is a file in
    the sandbox's infra area, rewritten once per turn — it cannot be the storage
    because ``Sandbox.kill`` rmtrees the whole sandbox root (the idle reaper
    fires it) and ``NfsArchive.persist``/``restore`` carry only the *workspace*,
    so a sandbox-only file loses the user's keys the moment the item goes idle.
    Deliberately NOT kept in the workspace either: ``read_file`` is
    workspace-confined, and that confinement is the only thing standing between
    the agent and a trivial read.

    Reading and writing ride the item's own ``permission`` (``write_meta``);
    injection does not go through the UI, so a member who may not edit still
    gets the variables in their turns."""

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
