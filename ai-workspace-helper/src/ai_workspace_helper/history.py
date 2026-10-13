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
        self._rows: list[dict[str, Any]] = self._read()
        #: Changes on every `add`: the window redraws the tab when it does.
        self.version = 0

    def _read(self) -> list[dict[str, Any]]:
        if self._path is None:
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        rows = []
        self._lines = len(text.splitlines())
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
                text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self._rows)
                self._path.write_text(text, encoding="utf-8")
                self._lines = len(self._rows)
            else:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def recent(self) -> list[dict[str, Any]]:
        """The newest first."""
        with self._lock:
            return list(reversed(self._rows))


__all__ = ["KEEP", "History"]
