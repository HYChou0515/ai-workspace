"""Background jobs on the usage record (docs/plan-capacity-usage.md §5.2 `job`,
§6.4): each attempt's requester, wait and run time, charged as `job:<type>`
work while it runs, inside the job's own usage scope."""

from __future__ import annotations

import datetime as dt
import time
from types import SimpleNamespace

import pytest

from workspace_app.capacity.jobs import recorded_job
from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.scope import UsageScope, current
from workspace_app.capacity.sink import UsageSink


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


def _job(created_by: str | None = "alice", retries: int = 0, ago_s: float = 30.0):
    created = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=ago_s)
    return SimpleNamespace(
        info=SimpleNamespace(created_by=created_by, created_time=created),
        data=SimpleNamespace(retries=retries, payload=None),
    )


def _usage(sink: _ListSink, kind: str) -> list[dict]:
    return [d["usage"] for d in sink.docs if d["usage"]["kind"] == kind]


def test_without_a_recorder_the_handler_is_untouched():
    def handle(job):  # noqa: ANN001, ANN202
        return None

    assert recorded_job("index", handle, None) is handle


def test_an_attempt_is_recorded_with_its_requester_wait_and_run_time():
    sink = _ListSink()
    rec = UsageRecorder(sink, component="worker", pod="w-1", window_s=300, queue_max=100)
    scopes: list[UsageScope | None] = []

    def handle(job):  # noqa: ANN001, ANN202
        scopes.append(current())
        time.sleep(0.05)

    job = _job(created_by="alice", retries=2, ago_s=30)
    recorded_job("index", handle, rec)(job)
    rec.close()

    (rec_job,) = _usage(sink, "job")
    enqueued = int(job.info.created_time.timestamp() * 1000)
    assert (rec_job["user"], rec_job["jobtype"], rec_job["outcome"], rec_job["attempt"]) == (
        "alice",
        "index",
        "done",
        3,
    )
    assert rec_job["enqueued_ms"] == enqueued
    assert rec_job["started_ms"] - enqueued >= 30_000, "it waited the 30 s since it was queued"
    assert rec_job["ended_ms"] - rec_job["started_ms"] >= 50
    assert scopes == [UsageScope(user="alice", purpose="index")]
    (work,) = _usage(sink, "work")
    assert (work["work"], work["user"], work["count"]) == ("job:index", "alice", 1)
    assert work["seconds"] >= 0.05


def test_a_failing_attempt_is_recorded_as_failed_and_still_fails():
    sink = _ListSink()
    rec = UsageRecorder(sink, component="worker", pod="w-1", window_s=300, queue_max=100)

    def handle(job):  # noqa: ANN001, ANN202
        raise RuntimeError("embedding endpoint down")

    with pytest.raises(RuntimeError, match="embedding endpoint down"):
        recorded_job("wiki", handle, rec)(_job())
    rec.close()

    assert [j["outcome"] for j in _usage(sink, "job")] == ["failed"]


def test_a_job_nobody_requested_is_the_systems():
    sink = _ListSink()
    rec = UsageRecorder(sink, component="worker", pod="w-1", window_s=300, queue_max=100)

    recorded_job("blob-gc", lambda job: None, rec)(_job(created_by=None))
    rec.close()

    assert [j["user"] for j in _usage(sink, "job")] == ["system"]
