"""The helper's own folder (docs/plan-outside-helper.md §3.7.1).

`%LOCALAPPDATA%\\OutsideHelper\\` on Windows; `OUTSIDE_HELPER_HOME` overrides it
(tests, a developer's Linux box). Holds the single-instance lock, the settings
the person gave on first start, the helper's own Chrome profile and its logs.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import IO, Any


def root() -> Path:
    override = os.environ.get("OUTSIDE_HELPER_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "OutsideHelper"
    return Path.home() / ".local" / "share" / "outside-helper"


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
    """Write through a temporary file + `os.replace`: a crash leaves the old
    settings or the new ones, never half of either."""
    home.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=home, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, home / "settings.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class InstanceLock:
    """`helper.lock`: an OS lock on an open file, so it goes away with the
    process however that ends — no stale lock after a crash."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self._path, "a+b")  # noqa: SIM115 — held open for the process's life
        try:
            _lock(f)
        except OSError:
            f.close()
            return False
        self._file = f
        return True

    def release(self) -> None:
        if self._file is not None:
            _unlock(self._file)
            self._file.close()
            self._file = None


if sys.platform == "win32":  # pragma: no cover — verified on Windows by hand (D14)
    import msvcrt

    def _lock(f: IO[bytes]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f: IO[bytes]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(f: IO[bytes]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f: IO[bytes]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


__all__ = ["InstanceLock", "profile_dir", "read_settings", "root", "write_settings"]
