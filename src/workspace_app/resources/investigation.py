from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from msgspec import Struct, field
from specstar import OnDelete, Ref


class Severity(StrEnum):
    """Investigation severity per the RCA design's P-rating.
    UI maps these to color tones (FE concern, not stored here)."""

    P0 = "P0"  # halt
    P1 = "P1"  # critical
    P2 = "P2"  # major
    P3 = "P3"  # minor
    P4 = "P4"  # cosmetic


class Status(StrEnum):
    """Investigation status flow.

    create → TRIAGING → AWAITING_REVIEW → RESOLVED  (happy path)
                                       └→ ABANDONED  (closed without RC)

    Design's `draft` is dropped — investigations always start at TRIAGING.
    """

    TRIAGING = "triaging"
    AWAITING_REVIEW = "awaiting_review"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class Investigation(Struct):
    """Top-level RCA investigation. Replaces the prior workspace-app
    `Workspace` resource (see plan-backend.md §3)."""

    title: str
    """Required. Short headline shown in the home table + breadcrumb."""

    owner: str
    """Required. User id of the creator — resolved via company API.
    v1 always `"default-user"` until SSO lands."""

    description: str = ""
    """Multi-line free text. Replaces the design's "Initial brief" field."""

    severity: Severity = Severity.P2
    status: Status = Status.TRIAGING

    product: str = ""
    """Part / board, e.g. "MX-7 board"."""

    members: list[str] = field(default_factory=list)
    """Additional user ids (excluding owner)."""

    topics: list[str] = field(default_factory=list)
    """Free-form tags shown in the home sidebar's TOPICS section
    (e.g. "Reflow zone-3", "Cell test fixture")."""

    attached_agent_config_id: Annotated[
        str | None, Ref("agent-config", on_delete=OnDelete.set_null)
    ] = None
    """Which AgentConfig drives this investigation's agent. If the
    referenced config is deleted, the back-pointer auto-clears."""

    template_profile: str = "default"
    """The template profile this investigation was seeded from. Persisted so
    the agent's system prompt can be composed with that template's starting-
    files appendix at turn time (see rca.templates.compose_system_prompt)."""
