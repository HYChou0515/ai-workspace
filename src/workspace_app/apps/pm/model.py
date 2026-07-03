"""The Project-Management App's own resource (#419).

``PmProject`` is PM's ``WorkItem`` — a per-App specstar resource (data is not
mixed across Apps). The item is deliberately thin: the real structure lives in
**entities** (file-first ``issues/N.md`` / ``milestones/N.md`` inside the item's
workspace, #419), not in more typed item columns. So the only Tier 3 domain
field is ``status`` (active / archived), which drives the lifecycle Close
affordance and indexes natively via ``INDEXED_FIELDS``.
"""

from __future__ import annotations

from enum import StrEnum

from ..base import IndexedFields, WorkItemBase


class Status(StrEnum):
    """A project's lifecycle. ``active`` while work is ongoing; ``archived`` once
    it's parked (the one closing state)."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class PmProject(WorkItemBase):
    # Tier 3 domain field (typed → indexes natively via INDEXED_FIELDS).
    status: Status = Status.ACTIVE


# What the App exposes to the platform registrar (apps/registry.py).
MODEL = PmProject
INDEXED_FIELDS: IndexedFields = ["status"]
