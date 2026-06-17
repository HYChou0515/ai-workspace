"""In-process activity log — backs GET /activity and the Home
notifications popover.

Records coarse-grained, user-visible events (investigation created /
closed, files written / moved / deleted, agent turn finished) in a
bounded ring buffer. Not persisted: a restart starts fresh, same as
MemoryFileStore. Swap for a specstar-backed resource if durable audit
history is ever needed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class ActivityEntry:
    ts: str  # ISO-8601 UTC
    kind: str  # investigation_created | investigation_closed | file_written | ...
    text: str  # human-readable one-liner
    ref: dict[str, str | int] = field(default_factory=dict)  # {investigation_id, path?, count?}


class ActivityLog:
    def __init__(self, maxlen: int = 200) -> None:
        self._entries: deque[ActivityEntry] = deque(maxlen=maxlen)

    def record(self, kind: str, text: str, ref: dict[str, str | int] | None = None) -> None:
        self._entries.appendleft(
            ActivityEntry(
                ts=datetime.now(UTC).isoformat(),
                kind=kind,
                text=text,
                ref=ref or {},
            )
        )

    def entries(self) -> list[dict]:
        """Newest-first, JSON-ready."""
        return [asdict(e) for e in self._entries]
