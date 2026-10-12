"""The errands this helper ships with (docs/plan-outside-helper.md D9), keyed
by the job `kind` the backend hands out."""

from __future__ import annotations

from .errands import Errand

BUILTIN: dict[str, Errand] = {}

__all__ = ["BUILTIN"]
