"""The Playground App's WorkItem (#89, #102 diagnostic).

A deliberately minimal App that sits next to ``apps/rca/`` so the workspace
surfaces (file tree + editor + terminal + sandbox ``exec``) can be exercised
against a clean baseline — no RCA domain fields, no package tools. Handy for
deciding whether a workspace quirk is the platform or a deploy's config.

Tier 1 (``title`` / ``owner`` / ``description`` / ``profile`` /
``attached_preset``) comes free from ``WorkItemBase``; ``topics`` is the Tier-2
opt-in (sidebar tags); ``status`` is the only typed domain field.
"""

from __future__ import annotations

from enum import StrEnum

from msgspec import field

from ..base import IndexedFields, WorkItemBase


class Status(StrEnum):
    """Scratch lifecycle; the closing state goes in app.json `lifecycle`."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class PlaygroundItem(WorkItemBase):
    # Tier 2 opt-in — free-form tags for sidebar grouping.
    topics: list[str] = field(default_factory=list)
    # Tier 3 — the single typed domain field (indexes natively via INDEXED_FIELDS).
    status: Status = Status.OPEN


# What the platform registrar scans for: the WorkItem Struct + indexed fields.
MODEL = PlaygroundItem
INDEXED_FIELDS: IndexedFields = ["status"]
