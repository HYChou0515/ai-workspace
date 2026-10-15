"""Background jobs on the usage record (docs/plan-capacity-usage.md §6.4).

Every coordinator registers its handler as
`job_handler=recorded_job("<jobtype>", self._handle, usage_recorder)`;
`tests/capacity/test_jobs_registration.py` fails when one does not. The wrapper
charges the attempt as `job:<jobtype>` work while it runs, runs it inside a
usage scope (so its LLM calls carry the requester's tags), and writes a `job`
record when it ends. Specstar records no start time, so the wait — start minus
`job.info.created_time` — exists only here.

The requester is `job.info.created_by`. Eval, graph, sanity and blob-gc create
their jobs without `using(...)`, so theirs is whatever the spec's default user
resolved to where they were enqueued: the requester inside a request, the
fallback user in a sweeper. Recorded as is.
"""

from __future__ import annotations

import contextlib
import functools
import time
from collections.abc import Callable
from typing import Any

from .recorder import UsageRecorder
from .records import JobRecord
from .scope import SYSTEM, UsageScope, scoped


def _ms(moment: Any) -> int:
    return int(moment.timestamp() * 1000)


def recorded_job(
    jobtype: str, handler: Callable[[Any], None], recorder: UsageRecorder | None
) -> Callable[[Any], None]:
    """`handler`, recorded. Without a recorder, `handler` itself."""
    if recorder is None:
        return handler

    @functools.wraps(handler)
    def run(job: Any) -> None:
        started = int(time.time() * 1000)
        user = job.info.created_by or SYSTEM
        running = recorder.begin(user, "", f"job:{jobtype}")
        outcome = "failed"
        try:
            with scoped(UsageScope(user=user, purpose=jobtype)):
                handler(job)
            outcome = "done"
        finally:
            running.end()
            # Counting is never a reason for a job to fail.
            with contextlib.suppress(Exception):
                recorder.emit(
                    JobRecord(
                        user=user,
                        jobtype=jobtype,
                        enqueued_ms=_ms(job.info.created_time),
                        started_ms=started,
                        ended_ms=int(time.time() * 1000),
                        outcome=outcome,
                        attempt=job.data.retries + 1,
                    ),
                    at_ms=started,
                )

    return run
