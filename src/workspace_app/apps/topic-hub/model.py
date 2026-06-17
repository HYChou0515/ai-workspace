"""The Topic Hub App's WorkItem (slug ``topic-hub``).

Loaded by **file path** (the slug is hyphenated, so ``apps.topic-hub`` is not an
importable package) — therefore this module uses **absolute imports** and **does
not** ``from __future__ import annotations`` (msgspec resolves the Struct's field
types eagerly; a path-exec'd module's synthetic name isn't reliably in
``sys.modules`` for stringised forward-ref resolution). Same reasoning as the
workflow ``run.py`` files and ``api/context_card_routes.py``.

The Hub's **collection set is a workspace file** (``collections.json``), not a
model field — see ``docs/topic-hub.md`` §5.
"""

from enum import StrEnum

from msgspec import field

from workspace_app.apps.base import IndexedFields, WorkItemBase


class Status(StrEnum):
    """A Hub stays ``active`` until it is ``archived`` (the closing state)."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class TopicHub(WorkItemBase):
    # Tier 2 (opt-in) — a Hub is collaborative (#43) and groupable.
    members: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    # Tier 3 — the only typed domain field; the collection set is a file (§5).
    status: Status = Status.ACTIVE


MODEL = TopicHub
INDEXED_FIELDS: IndexedFields = ["status"]
