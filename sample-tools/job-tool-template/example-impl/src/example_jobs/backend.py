"""A DEMO backend: a job is a file in the workspace that finishes after a set
number of seconds.

It talks to nothing, so it is wrong for real work and right for seeing the
template run end to end. Replace this package with one that talks to your
scheduler; keep its shape.

What it shows a real backend doing:
- the caller comes from the credential (here a plain variable, DEMO_JOBS_USER),
  and a missing one is `NeedsAction` naming the variable;
- the same key twice is one job;
- another caller's job does not exist for you (`NotFound`);
- `phase` is the template's three words; `state` is this backend's own.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from job_contract import (
    Contract,
    EnvVar,
    Idempotency,
    JobBackend,
    JobError,
    JobStatus,
    NeedsAction,
    NotFound,
    Retryable,
)

#: Where the jobs live, inside the workspace (the working directory).
STORE = Path(".demo-jobs")


class DemoBackend(JobBackend):
    contract = Contract(
        domain_description=(
            "Runs a DEMO job: it does nothing for the given number of seconds, then "
            "finishes with a one-line result."
        ),
        spec_schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What to call the job."},
                "seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3600,
                    "description": "How long the job runs before it finishes.",
                },
                "label": {
                    "type": "string",
                    "description": "Change it to run the same task again as a new job.",
                },
            },
            "required": ["task", "seconds"],
        },
        idempotency=Idempotency(fields=("task", "seconds", "label")),
        patch_schema={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3600,
                    "description": "A new total run time; only a running job can change.",
                }
            },
            "required": ["seconds"],
        },
        env=(
            EnvVar(
                "DEMO_JOBS_USER",
                "Who you are to the demo backend; any name. Jobs are listed per name.",
                required=True,
            ),
        ),
    )

    # ── storage ─────────────────────────────────────────────────────────────

    def _records(self) -> list[dict[str, Any]]:
        if not STORE.is_dir():
            return []
        return [json.loads(path.read_text()) for path in STORE.glob("*.json")]

    def _save(self, record: dict[str, Any]) -> None:
        STORE.mkdir(exist_ok=True)
        (STORE / f"{record['job_id']}.json").write_text(json.dumps(record))

    def _mine(self, job_id: str) -> dict[str, Any]:
        path = STORE / f"{job_id}.json"
        record = json.loads(path.read_text()) if path.is_file() else None
        if record is None or record["caller"] != self.caller_id():
            raise NotFound(f"no job {job_id!r} for {self.caller_id()}")
        return record

    def _status(self, record: dict[str, Any]) -> JobStatus:
        elapsed = time.time() - record["submitted_at"]
        detail = {"task": record["spec"]["task"], "seconds": record["seconds"]}
        if record["cancelled"]:
            return JobStatus(record["job_id"], "done", "cancelled", detail)
        if elapsed >= record["seconds"]:
            return JobStatus(record["job_id"], "done", "succeeded", detail)
        detail["remaining"] = round(record["seconds"] - elapsed)
        return JobStatus(record["job_id"], "running", "running", detail)

    # ── required ────────────────────────────────────────────────────────────

    def caller_id(self) -> str:
        user = os.environ.get("DEMO_JOBS_USER", "").strip()
        if not user:
            raise NeedsAction("set DEMO_JOBS_USER to say who you are to the demo backend")
        return user

    def submit(self, spec: dict[str, Any], key: str) -> str:
        existing = self.find_by_key(key)
        if existing is not None:
            return existing.job_id
        record = {
            "job_id": key[:12],
            "key": key,
            "caller": self.caller_id(),
            "spec": spec,
            "seconds": spec["seconds"],
            "submitted_at": time.time(),
            "cancelled": False,
        }
        self._save(record)
        return record["job_id"]

    def find_by_key(self, key: str) -> JobStatus | None:
        for record in self._records():
            if record["key"] == key and record["caller"] == self.caller_id():
                return self._status(record)
        return None

    def get(self, job_id: str) -> JobStatus:
        return self._status(self._mine(job_id))

    def list(self, cursor: str | None, limit: int) -> tuple[list[JobStatus], str | None]:
        mine = [r for r in self._records() if r["caller"] == self.caller_id()]
        mine.sort(key=lambda r: r["submitted_at"], reverse=True)
        start = int(cursor or 0)
        page = mine[start : start + limit]
        after = str(start + limit) if start + limit < len(mine) else None
        return [self._status(r) for r in page], after

    # ── optional ────────────────────────────────────────────────────────────

    def update(self, job_id: str, patch: dict[str, Any]) -> JobStatus:
        record = self._mine(job_id)
        if self._status(record).phase == "done":
            raise NeedsAction(f"job {job_id} already finished; submit a new one instead")
        record["seconds"] = patch["seconds"]
        self._save(record)
        return self._status(record)

    def cancel(self, job_id: str) -> JobStatus:
        record = self._mine(job_id)
        if self._status(record).phase == "running":
            record["cancelled"] = True
            self._save(record)
        return self._status(record)

    def logs(self, job_id: str, offset: int, limit: int) -> tuple[str, int]:
        status = self._status(self._mine(job_id))
        ran = status.detail["seconds"] - status.detail.get("remaining", 0)
        log = "".join(f"second {n} of {status.detail['seconds']}\n" for n in range(1, ran + 1))
        log += f"{status.state}\n" if status.phase == "done" else ""
        return log[offset : offset + limit], len(log)

    def fetch(self, job_id: str, name: str, dest: str) -> str:
        status = self._status(self._mine(job_id))
        if name != "result":
            raise NotFound(f"job {job_id} has one output, 'result'; there is no {name!r}")
        if status.phase == "running":
            raise Retryable(f"job {job_id} is still running; its result exists once it succeeds")
        if status.state != "succeeded":
            # Never coming: saying "try again" here would send the model round in circles.
            raise JobError(f"job {job_id} was {status.state}; it has no result")
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{status.detail['task']} finished after {status.detail['seconds']}s\n")
        return dest
