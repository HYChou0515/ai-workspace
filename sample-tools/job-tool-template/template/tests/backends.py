"""Backends that exist only so the shell has something to describe.

They answer with fixed values. What the shell is tested on is its own work —
which commands it offers, what it says, how it ends — never a backend's.
"""

from __future__ import annotations

import atexit
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from job_contract import Budget, Contract, Idempotency, JobBackend, JobStatus, Submitted

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

    def submit(self, spec, key, item_id):
        return Submitted("j1", created=True)

    def find_by_key(self, key):
        return None

    def get(self, job_id):
        return JobStatus(job_id=job_id, phase="running", state="queued")

    def list(self, cursor, limit):
        return [], None


class NoGet(JobBackend):
    """Forgot `get`: definable, but never loadable."""

    contract = Minimal.contract

    def submit(self, spec, key, item_id):
        return Submitted("j1", created=True)

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


class Shelling(Minimal):
    """`submit` runs a command, the way a backend calls `sbatch` or `kubectl`.
    The child writes to file descriptor 1, which no `sys.stdout` swap reaches."""

    def submit(self, spec, key, item_id):
        subprocess.run(["echo", "CHILD-OUTPUT"], check=True)
        return Submitted("j1", created=True)


class Lingering(Minimal):
    """`get` answers in time but leaves work behind that speaks after the
    answer: a pool job (the interpreter waits for it at exit) and an atexit
    handler. Both run after `main` has returned."""

    def get(self, job_id):
        def later():
            time.sleep(0.3)
            print("POOL-NOISE")
            subprocess.run(["echo", "POOL-CHILD"], check=True)

        pool = ThreadPoolExecutor(1)
        pool.submit(later)
        pool.shutdown(wait=False)
        atexit.register(lambda: print("ATEXIT-NOISE"))
        return JobStatus(job_id=job_id, phase="running", state="queued")


class _SetOrder(Idempotency):
    """Sameness through a set: the same within one process, different in the
    next — string hashing is randomised per process, and every tool call is a
    new process."""

    def identity(self, spec):
        return list({spec["image"], spec["command"], "a", "b", "c", "d", "e", "f"})


class _Lowercase(Idempotency):
    def identity(self, spec):
        return {"image": spec["image"].lower(), "command": spec["command"].strip()}


class SteadyOverride(Minimal):
    """An overridden sameness that is the same in every process."""

    contract = Contract(
        spec_schema=SPEC,
        idempotency=_Lowercase(fields=("image", "command")),
        example_spec={"image": "Trainer:3", "command": " train.py "},
    )


class _PairOrder(Idempotency):
    """Two strings in a set: only two orders, so few seeds can all agree."""

    def identity(self, spec):
        return list({spec["image"], spec["command"]})


class PairDrift(Minimal):
    # The example review found: five seeds all put these two in one order.
    contract = Contract(
        spec_schema=SPEC,
        idempotency=_PairOrder(fields=("image", "command")),
        example_spec={"image": "img23", "command": "train.py"},
    )


class Holder:
    """A backend reached by a dotted path, as an entry point may name it."""

    Inner = SteadyOverride


class HashDrift(Minimal):
    contract = Contract(
        spec_schema=SPEC,
        idempotency=_SetOrder(fields=("image", "command")),
        example_spec={"image": "trainer:3", "command": "train.py"},
    )


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

    def __init__(self):
        # Building a backend may itself be slow (connecting to the scheduler);
        # a delay under "__init__" stands for that.
        time.sleep(type(self).delay.get("__init__", 0))

    def submit(self, spec, key, item_id):
        return self._answer("submit", spec, key, item_id)

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
