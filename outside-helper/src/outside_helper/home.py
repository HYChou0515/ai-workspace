"""The helper's own folder (docs/plan-outside-helper.md §3.7.1).

`%LOCALAPPDATA%\\OutsideHelper\\` on Windows; `OUTSIDE_HELPER_HOME` overrides it
(tests, a developer's Linux box). Holds the single-instance lock, the settings
the person gave on first start, the helper's own Chrome profile and its logs.

The folder, the lock and the atomic writer are `setup_core`'s — one definition
shared with the installer, the launcher and the uninstaller, which run with no
package installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import setup_core

root = setup_core.root
InstanceLock = setup_core.InstanceLock


def profile_dir(home: Path) -> Path:
    """The helper's own Chrome profile — outside the version folders, so an
    update does not sign the person out (§3.7.7)."""
    return home / "profile"


def read_settings(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(home: Path, settings: dict[str, Any]) -> None:
    """Atomic: a crash leaves the old settings or the new ones, never half."""
    setup_core.write_json(home / "settings.json", settings)


__all__ = ["InstanceLock", "profile_dir", "read_settings", "root", "write_settings"]
