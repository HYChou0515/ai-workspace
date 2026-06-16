"""The _template App's WorkItem — copy this dir + rename for a new App.

Replace ``TemplateItem`` with your App's name, the enums + Tier-3 fields with
your domain, and ``INDEXED_FIELDS`` with the fields you filter / sort / colour
on (must be typed — enums or scalars). Tier 1 (``title`` / ``owner`` /
``description`` / ``profile`` / ``attached_preset``) comes free from
``WorkItemBase``; Tier 2 (``members`` / ``topics``) is opt-in — redeclare them as
concrete lists if your App uses them. See ``docs/adding-an-app.md``.
"""

from __future__ import annotations

from enum import StrEnum

from ..base import IndexedFields, WorkItemBase


class Priority(StrEnum):
    """Option order = sort rank (high first)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(StrEnum):
    """The item lifecycle; the closing states go in app.json `lifecycle`."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DROPPED = "dropped"


class TemplateItem(WorkItemBase):
    # Tier 3 — your App's typed domain fields (index natively via INDEXED_FIELDS).
    priority: Priority = Priority.MEDIUM
    status: Status = Status.OPEN


# What the platform registrar reads (apps/registry.py scans for these): the
# WorkItem Struct + the fields to index for list filter / sort.
MODEL = TemplateItem
INDEXED_FIELDS: IndexedFields = ["priority", "status"]
