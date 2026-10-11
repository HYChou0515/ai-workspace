"""Backends that exist only so the shell has something to describe.

They answer with fixed values. What the shell is tested on is its own work —
which commands it offers, what it says, how it ends — never a backend's.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from job_contract import Budget, Contract, Idempotency, JobBackend, JobStatus

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


class NoGet(JobBackend):
    """Forgot `get`: definable, but never loadable."""

    contract = Minimal.contract

    def caller_id(self):
        return "ann"

    def submit(self, spec, key):
        return "j1"

    def find_by_key(self, key):
        return None

    def list(self, cursor, limit):
        return [], None


class Pooled(Minimal):
    """`get` waits on a thread pool — what boto3, gRPC and most SDKs do. The
    pool's worker is not a daemon, and the interpreter joins it at exit."""

    contract = Contract(
        spec_schema=SPEC,
        idempotency=Idempotency(fields=("image", "command")),
        budget=Budget(get=0.2),
    )

    def get(self, job_id):
        with ThreadPoolExecutor(1) as pool:
            return pool.submit(time.sleep, 30).result()


class Scripted(Minimal):
    """Answers with whatever the test put in `answers`, or raises it, after
    `delay` seconds. A real thread really waits: the budget is real too."""

    contract = Contract(
        spec_schema=SPEC,
        idempotency=Idempotency(fields=("image", "command")),
        budget=Budget(submit=0.2, find_by_key=0.2, get=0.2, list=0.2),
    )
    answers: dict = {}
    delay: dict = {}
    calls: list = []

    def _answer(self, call, *args):
        type(self).calls.append((call, *args))
        time.sleep(type(self).delay.get(call, 0))
        answer = type(self).answers[call]
        if isinstance(answer, BaseException):  # SystemExit too: backends do call sys.exit
            raise answer
        return answer

    def caller_id(self):
        return self._answer("caller_id")

    def submit(self, spec, key):
        return self._answer("submit", spec, key)

    def find_by_key(self, key):
        return self._answer("find_by_key", key)

    def get(self, job_id):
        return self._answer("get", job_id)

    def list(self, cursor, limit):
        return self._answer("list", cursor, limit)


class ScriptedFull(Scripted):
    """`Scripted`, with every optional call too."""

    contract = Contract(
        spec_schema=SPEC,
        idempotency=Idempotency(fields=("image", "command")),
        budget=Budget(submit=0.2, find_by_key=0.2, get=0.2, list=0.2),
        patch_schema=PATCH,
    )

    def update(self, job_id, patch):
        return self._answer("update", job_id, patch)

    def cancel(self, job_id):
        return self._answer("cancel", job_id)

    def logs(self, job_id, offset, limit):
        return self._answer("logs", job_id, offset, limit)

    def fetch(self, job_id, name, dest):
        return self._answer("fetch", job_id, name, dest)


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
