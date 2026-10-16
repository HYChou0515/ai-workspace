"""The record kinds (docs/plan-capacity-usage.md §5.2) as documents: the
fields each kind carries, and the request-latency histogram that makes a p95
computable from sums alone."""

from workspace_app.capacity.records import (
    LATENCY_BOUNDS_MS,
    DiskRow,
    JobRecord,
    TurnRecord,
    WorkRow,
    latency_bucket,
    to_document,
)


def test_latency_buckets_are_upper_bounds_and_the_last_holds_everything_slower():
    assert len(LATENCY_BOUNDS_MS) == 9
    assert latency_bucket(0) == 0
    assert latency_bucket(50) == 0
    assert latency_bucket(51) == 1
    assert latency_bucket(30_000) == 8
    assert latency_bucket(30_001) == 9


def test_a_request_row_carries_only_its_nonzero_latency_buckets():
    hist = (0, 3, 0, 0, 0, 0, 0, 0, 0, 1)
    row = WorkRow(
        user="alice",
        app="pm",
        work="req:GET /api/x",
        count=4,
        seconds=31.2,
        window_s=300,
        hist=hist,
    )

    usage = to_document(row, 0, pod="p", component="api")["usage"]

    assert usage["kind"] == "work"
    assert (usage["user"], usage["app"], usage["work"], usage["count"], usage["seconds"]) == (
        "alice",
        "pm",
        "req:GET /api/x",
        4,
        31.2,
    )
    assert usage["window_s"] == 300
    assert {k: v for k, v in usage.items() if k.startswith("h")} == {"h1": 3, "h9": 1}


def test_a_row_without_a_histogram_has_no_bucket_fields():
    row = WorkRow(user="bob", app="", work="turn:chat", count=1, seconds=12.0, window_s=300)

    usage = to_document(row, 0, pod="p", component="api")["usage"]

    assert not [k for k in usage if k.startswith("h")]


def test_a_turn_without_output_leaves_first_output_out():
    rec = TurnRecord(
        user="alice",
        app="pm",
        item="i1",
        surface="chat",
        outcome="cancelled",
        accepted_ms=1,
        started_ms=2,
        first_event_ms=None,
        first_output_ms=None,
        ended_ms=9,
    )

    usage = to_document(rec, 1, pod="p", component="api")["usage"]

    assert usage["kind"] == "turn"
    assert usage["outcome"] == "cancelled"
    assert "first_output_ms" not in usage and "first_event_ms" not in usage
    assert (usage["accepted_ms"], usage["started_ms"], usage["ended_ms"]) == (1, 2, 9)


def test_job_and_disk_records_name_their_kind():
    job = JobRecord(
        user="u",
        jobtype="index",
        enqueued_ms=1,
        started_ms=5,
        ended_ms=8,
        outcome="done",
        attempt=1,
    )
    disk = DiskRow(scope="workspace", user="u", app="pm", bytes=1024, item="i1")

    assert to_document(job, 5, pod="p", component="worker")["usage"]["kind"] == "job"
    usage = to_document(disk, 0, pod="p", component="worker")["usage"]
    assert usage["kind"] == "disk"
    assert usage["item"] == "i1" and "collection" not in usage
