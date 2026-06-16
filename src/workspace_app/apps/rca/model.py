"""The RCA App's own resource (#89).

`RcaInvestigation` is RCA's `WorkItem` — a per-App specstar resource (data is
not mixed across Apps). Its Tier 3 domain fields (severity / status / product)
are typed (enums), so they index natively via `INDEXED_FIELDS`.
"""

from __future__ import annotations

from enum import StrEnum

from msgspec import field

from ..base import IndexedFields, WorkItemBase


class Severity(StrEnum):
    """RCA P-rating. Option order = sort rank (P0 highest)."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class Status(StrEnum):
    """RCA investigation lifecycle.

    triaging → awaiting_review → resolved (happy path) / abandoned (closed
    without a root cause).
    """

    TRIAGING = "triaging"
    AWAITING_REVIEW = "awaiting_review"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class RcaInvestigation(WorkItemBase):
    # Tier 2 opt-in: redeclare the base's `T | UnsetType` fields as concrete
    # lists so RCA has members + topics (default [], not UNSET).
    members: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    # Tier 3 domain fields (typed → index natively via INDEXED_FIELDS).
    severity: Severity = Severity.P2
    status: Status = Status.TRIAGING
    product: str = ""


# What the App exposes to the platform registrar (apps/registry.py). The
# registrar does `add_model(MODEL, indexed_fields=INDEXED_FIELDS)`; P3 turns the
# explicit registry into a scan of `apps/`.
MODEL = RcaInvestigation
INDEXED_FIELDS: IndexedFields = ["severity", "status", "product"]
