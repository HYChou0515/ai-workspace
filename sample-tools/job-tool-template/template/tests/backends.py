"""Backends that exist only so the shell has something to describe.

They answer with fixed values. What the shell is tested on is its own work —
which commands it offers, what it says, how it ends — never a backend's.
"""

from __future__ import annotations

from job_contract import Contract, Idempotency, JobBackend, JobStatus

SPEC = {
    "type": "object",
    "properties": {
        "image": {"type": "string", "description": "the container image to run"},
        "command": {"type": "string"},
    },
    "required": ["image", "command"],
}
PATCH = {"type": "object", "properties": {"priority": {"type": "integer"}}}


class Minimal(JobBackend):
    """Exactly the required calls."""

    contract = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image", "command")))

    def caller_id(self):
        return "ann"

    def submit(self, spec, key):
        return "j1"

    def find_by_key(self, key):
        return None

    def get(self, job_id):
        return JobStatus(job_id=job_id, phase="running", state="queued")

    def list(self, cursor, limit):
        return [], None


class Full(Minimal):
    """Every optional call as well."""

    contract = Contract(
        spec_schema=SPEC,
        idempotency=Idempotency(fields=("image", "command")),
        patch_schema=PATCH,
    )

    def update(self, job_id, patch):
        return self.get(job_id)

    def cancel(self, job_id):
        return self.get(job_id)

    def logs(self, job_id, offset, limit):
        return "", 0

    def fetch(self, job_id, name, dest):
        return dest
