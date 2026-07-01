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

    members: list[str] | UnsetType = UNSET
    """Tier 2 (opt-in) — collaborators. Redeclare as ``list[str]`` in the App's
    subclass to enable; left ``UNSET`` the App has no members concept."""

    topics: list[str] | UnsetType = UNSET
    """Tier 2 (opt-in) — free-form tags for sidebar grouping. Same opt-in rule."""
