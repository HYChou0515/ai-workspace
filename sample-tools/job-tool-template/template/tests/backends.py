"""An execution side that lives in memory, so the shell has something to talk to.

It keeps the promises the contract asks of every backend — write at the
offset, never truncate, start an id once — and lets a test slow a call down,
make it fail, or drop a piece on the floor. What is tested is the shell's work:
what it sends, when it starts, what it tells the model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from job_contract import (
    Budget,
    Capacity,
    Entry,
    JobBackend,
    NotFound,
    QueuedJob,
    Status,
    Worker,
)

RESOURCES = {
    "type": "object",
    "properties": {
        "gpus": {"type": "integer", "minimum": 0, "maximum": 8},
        "priority": {"enum": ["low", "normal", "high"]},
    },
    "required": ["gpus"],
    "additionalProperties": False,
}


@dataclass
class Machine:
    """The other side's state, shared by every instance a call builds."""

    files: dict[str, dict[str, bytearray]] = field(default_factory=dict)
    started: dict[str, dict[str, Any]] = field(default_factory=dict)
    statuses: dict[str, Status] = field(default_factory=dict)
    logs: dict[str, str] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)
    delay: dict[str, float] = field(default_factory=dict)
    raises: dict[str, BaseException] = field(default_factory=dict)
    linger: dict[str, float] = field(default_factory=dict)
    """Seconds a call waits AFTER doing its work: it landed, the answer is slow."""
    drop_pieces: int = 0
    """How many upload pieces to accept and then lose."""
    capacity: Capacity = field(default_factory=Capacity)

    def enter(self, call: str, *args: Any) -> None:
        self.calls.append((call, *args))
        time.sleep(self.delay.get(call, 0))
        error = self.raises.get(call)
        if error is not None:
            raise error

    def count(self, call: str) -> int:
        return sum(1 for c in self.calls if c[0] == call)


class Remote(JobBackend):
    run_description = "Runs the folder on the test machine: put the command in run.sh."
    resources_schema = RESOURCES
    budget = Budget(upload=0.5, start=0.5, status=0.5, list_files=0.5, download=0.5,
                    logs=0.5, cancel=0.5, capacity=0.5)  # fmt: skip
    machine: Machine = Machine()

    def upload(self, job_id, path, offset, data):
        m = self.machine
        m.enter("upload", job_id, path, offset, len(data))
        if m.drop_pieces:
            m.drop_pieces -= 1
            return
        held = m.files.setdefault(job_id, {}).setdefault(path, bytearray())
        if offset > len(held):
            held.extend(b"\0" * (offset - len(held)))
        held[offset : offset + len(data)] = data

    def start(self, job_id, resources):
        m = self.machine
        m.enter("start", job_id, resources)
        if job_id not in m.started:
            m.started[job_id] = resources
            m.statuses.setdefault(job_id, Status("queued", position=3))
        time.sleep(m.linger.get("start", 0))

    def status(self, job_id):
        m = self.machine
        m.enter("status", job_id)
        if job_id not in m.started:
            raise NotFound(f"no job {job_id}")
        return m.statuses[job_id]

    def list_files(self, job_id, path):
        m = self.machine
        m.enter("list_files", job_id, path)
        if job_id not in m.files:
            raise NotFound(f"no job {job_id}")
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"
        entries = []
        dirs = set()
        for name, data in sorted(m.files[job_id].items()):
            if not name.startswith(prefix) and name != path:
                continue
            parts = name.split("/")
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]))
            entries.append(Entry(name, is_dir=False, size=len(data)))
        entries += [Entry(d, is_dir=True) for d in sorted(dirs) if d.startswith(prefix)]
        return entries

    def download(self, job_id, path, offset, limit):
        m = self.machine
        m.enter("download", job_id, path, offset, limit)
        try:
            data = m.files[job_id][path]
        except KeyError:
            raise NotFound(f"no file {path}") from None
        return bytes(data[offset : offset + limit])

    def logs(self, job_id, offset, limit):
        m = self.machine
        m.enter("logs", job_id, offset, limit)
        text = m.logs.get(job_id, "")
        return text[offset : offset + limit], len(text)

    def cancel(self, job_id):
        m = self.machine
        m.enter("cancel", job_id)
        m.cancelled.append(job_id)
        if job_id in m.started:
            m.statuses[job_id] = Status("failed", state="cancelled")


class RemoteWithQueue(Remote):
    def capacity(self):
        self.machine.enter("capacity")
        return self.machine.capacity


def sample_capacity() -> Capacity:
    return Capacity(
        workers=(Worker("gpu-1", "busy", job_id="j-other", free={"gpus": 0}),
                 Worker("gpu-2", "idle", free={"gpus": 8})),
        queue=(QueuedJob("j-other-2", resources={"gpus": 8}, waiting_since="09:00"),),
        quota={"gpu_hours": "12 of 20"},
    )  # fmt: skip
