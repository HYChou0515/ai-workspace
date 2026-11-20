"""What the tool remembers about the jobs it sent: one file per job in the
workspace, `jobs/<job_id>/job.json` (docs/plan-job-queue.md D12).

It is in the workspace so a person sees it in the file tree, and it is how
`jobs` knows which jobs are this item's. It is the tool's memory, never the
truth about the job: where a job is, and how much of it has arrived, is
always asked of the backend.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: The records folder, at the top of the workspace. Never sent (D12).
FOLDER = "jobs"
FILE = "job.json"


@dataclass
class Record:
    job_id: str
    hash: str
    attempt: int
    workdir: str
    resources: dict[str, Any]
    item_id: str | None
    files: int
    bytes: int
    created_at: float
    started: bool = False
    started_at: float | None = None
    last: dict[str, Any] | None = field(default=None)
    """The last status seen, for the person reading the file."""


def folder() -> Path:
    return Path.cwd() / FOLDER


def save(record: Record) -> None:
    """Write the record whole or not at all: a call cut off halfway must not
    leave a file that no longer parses."""
    target = folder() / record.job_id / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(record), indent=2, ensure_ascii=False), "utf-8")
    os.replace(temporary, target)


def _read(path: Path) -> Record | None:
    try:
        return Record(**json.loads(path.read_text("utf-8")))
    except (OSError, ValueError, TypeError):
        return None  # not ours, or cut off: never a reason to fail a call


def load(job_id: str) -> Record | None:
    return _read(folder() / job_id / FILE)


def every() -> list[Record]:
    """Every record in the workspace, oldest first."""
    found = [_read(path) for path in folder().glob(f"*/{FILE}")]
    return sorted((r for r in found if r is not None), key=lambda r: (r.created_at, r.job_id))


def latest_for(job_hash: str) -> Record | None:
    """The newest attempt at this exact request, if any."""
    same = [r for r in every() if r.hash == job_hash]
    return max(same, key=lambda r: r.attempt) if same else None
