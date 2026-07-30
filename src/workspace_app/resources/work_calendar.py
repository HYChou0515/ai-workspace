"""The deployment's work calendar — which days people are actually in the office (#615 P1).

ONE row for the whole deployment (``resource_id == "default"``), so every pod
reads it by a point key. Registered post-``spec.apply`` via
``register_work_calendar`` (NOT in ``_register_all``), so specstar mounts no
bare auto-CRUD routes for it — a new open route family was #607's defect class;
the gated ``/work-calendar`` endpoints are the only wire surface.

It is a stored resource rather than a config key because it is edited far more
often than a deploy: Taiwan's flexible holidays move every year, and each one
brings a make-up workday with it. Config would mean editing config.yaml and
restarting pods to record that next Saturday is a working day.
"""

from __future__ import annotations

import contextlib

from msgspec import Struct, field
from specstar import SpecStar
from specstar.types import (
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

CALENDAR_ID = "default"
"""The single row's id — one calendar per deployment, not per user."""

OVERRIDE_VALUES = ("off", "work")
"""``off`` frees a working day (a public holiday); ``work`` claims a
non-working one (a make-up workday). BOTH directions are load-bearing."""

DEFAULT_WORKDAYS = [0, 1, 2, 3, 4]
"""Monday=0 … Sunday=6 — the calendar an unconfigured deployment reads as."""


class WorkCalendar(Struct):
    workdays: list[int] = field(default_factory=lambda: list(DEFAULT_WORKDAYS))
    """Which weekdays are working days (Monday=0 … Sunday=6)."""

    overrides: dict[str, str] = field(default_factory=dict)
    """``"YYYY-MM-DD" -> "off" | "work"``, overriding the weekday rule for
    that one date."""


def register_work_calendar(spec: SpecStar) -> None:
    """Idempotently register the calendar model. Safe to call on every pod."""
    with contextlib.suppress(ValueError):
        spec.add_model(WorkCalendar)


def read_work_calendar(spec: SpecStar) -> WorkCalendar:
    """The deployment's calendar. An absent row is the DEFAULT calendar, not an
    error — a deployment that never configured one still has working days."""
    rm = spec.get_resource_manager(WorkCalendar)
    try:
        res = rm.get(CALENDAR_ID)
    except (ResourceIDNotFoundError, ResourceIsDeletedError):
        return WorkCalendar()
    data = res.data
    assert isinstance(data, WorkCalendar)  # narrow Struct|Unset for ty
    return data


def upsert_work_calendar(spec: SpecStar, cal: WorkCalendar, *, user: str) -> None:
    """Whole-overwrite the deployment's calendar, attributed to ``user``.

    Modify-first, create when absent — the same point-key write as
    ``_SandboxActivity.bump`` and the goal row. There is no soft-delete branch
    to carry: this row is never deleted, because 'no calendar' is not a state a
    deployment can be in — `read_work_calendar` already answers that with the
    default working week."""
    rm = spec.get_resource_manager(WorkCalendar)
    with rm.using(user=user):
        try:
            rm.modify(CALENDAR_ID, cal, status=RevisionStatus.draft)
        except ResourceIDNotFoundError:
            rm.create(cal, resource_id=CALENDAR_ID, status=RevisionStatus.draft)
