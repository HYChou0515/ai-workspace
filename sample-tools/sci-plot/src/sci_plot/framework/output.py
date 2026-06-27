"""Where a chart's PNG is written.

Default: ``charts/<chart>_<timestamp>.png`` (relative to the sandbox cwd = the
workspace root, so the FileStore syncs it back). **Timestamp, not a hash** —
``<chart>_20260627-143501-204193.png``; duplicate-collision handling is
deliberately deferred ("有重複再說"). The caller may pass an explicit ``output``
path to override.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

_DEFAULT_DIR = "charts"


def default_filename(chart_name: str, now: datetime) -> str:
    return f"{chart_name}_{now:%Y%m%d-%H%M%S-%f}.png"


def resolve_output(chart_name: str, explicit: str | None, now: datetime) -> Path:
    """Return the path to write, creating its parent directory."""
    path = Path(explicit) if explicit else Path(_DEFAULT_DIR) / default_filename(chart_name, now)
    parent = path.parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
    return path
