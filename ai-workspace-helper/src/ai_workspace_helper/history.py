"""The window's 查詢紀錄 (docs/plan-outside-helper.md D13): what the helper ran
on this PC — when, what, on which site, and how it ended.

Kept only here (`history.jsonl` in the helper's folder, gone with an
uninstall), never sent anywhere: the card already shows each job's state. One
JSON object per line; the newest `keep` are what the window shows, and the
file is rewritten to those whenever it has grown to twice that. A line that
does not read is skipped — a history is not worth failing a start over.

The worker thread adds, the window thread reads: both under one lock.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Rows the window keeps.
KEEP = 200

_FIELDS = ("at", "kind", "args", "site", "outcome", "reason")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class History:
    def __init__(
        self, path: Path | None, *, keep: int = KEEP, clock: Callable[[], str] = _now
    ) -> None:
        #: None keeps it in memory only (tests, the live check).
        self._path = path
        self._keep = keep
        self._clock = clock
        self._lock = threading.Lock()
        #: Lines in the file now — it is rewritten once that reaches 2 × keep.
        self._lines = 0
        #: The file ends inside a line (cut short by a crash): the next row
        #: starts on a line of its own rather than glued onto that one.
        self._cut = False
        self._rows: list[dict[str, Any]] = self._read()
        #: Changes on every `add`: the window redraws the tab when it does.
        self.version = 0

    def _read(self) -> list[dict[str, Any]]:
        if self._path is None:
            return []
        try:
            # a line cut inside a character (a crash mid-append) or a file
            # re-saved in another encoding only spoils its own lines
            text = self._path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        rows = []
        self._lines = len(text.splitlines())
        self._cut = bool(text) and not text.endswith("\n")
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append({k: str(row.get(k, "")) for k in _FIELDS})
        return rows[-self._keep :]

    def add(self, *, kind: str, args: str, site: str, outcome: str, reason: str = "") -> None:
        row = {
            "at": self._clock(),
            "kind": kind,
            "args": args,
            "site": site,
            "outcome": outcome,
            "reason": reason,
        }
        with self._lock:
            self._rows = [*self._rows, row][-self._keep :]
            self.version += 1
            if self._path is None:
                return
            self._lines += 1
            if self._lines >= 2 * self._keep:
                # whole or not at all: written beside it, then moved over it
                text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self._rows)
                beside = self._path.with_name(self._path.name + ".new")
                beside.write_text(text, encoding="utf-8")
                os.replace(beside, self._path)
                self._lines = len(self._rows)
            else:
                with self._path.open("a", encoding="utf-8") as f:
                    line = json.dumps(row, ensure_ascii=False) + "\n"
                    f.write(("\n" if self._cut else "") + line)
            self._cut = False

    def recent(self) -> list[dict[str, Any]]:
        """The newest first."""
        with self._lock:
            return list(reversed(self._rows))


__all__ = ["KEEP", "History"]
