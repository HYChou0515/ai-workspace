"""The one diagnostic shape (#419 §E). Parser, schema loading, and discovery
all report through `(renderable result, list[Diagnostic])` so a bad file
degrades to a warning/error rather than an exception."""

from __future__ import annotations

import msgspec


class Diagnostic(msgspec.Struct, frozen=True):
    level: str
    """`"warning"` (lint, still usable) or `"error"` (dropped from projection)."""
    message: str
    field: str | None = None
