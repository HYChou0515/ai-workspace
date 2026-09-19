"""The progress file (plan-chat-video-export decisions 11–12): one JSON
document beside the output, written by the API when a job is queued and by
the worker every heartbeat; the FE polls it, its absence is the cancel, a
heartbeat that stops is a dead worker.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from workspace_app.chat_video.progress import Progress, is_alive, paths_for

T0 = datetime(2026, 9, 19, 3, 10, tzinfo=UTC)


def test_the_files_beside_an_output_are_named_from_it():
    """Three files, one stem: the video, the transcript it was made from
    (kept — edit and resubmit), the progress that doubles as the cancel."""
    assert paths_for("/exports/chat-video/OOM-20260919-0310.mp4") == (
        "/exports/chat-video/OOM-20260919-0310.mp4.chat.json",
        "/exports/chat-video/OOM-20260919-0310.mp4.progress.json",
    )


def test_a_progress_document_is_human_readable_json_and_round_trips():
    p = Progress(
        stage="rendering",
        expected_seconds=41,
        elapsed_seconds=20,
        started_at=T0,
        heartbeat_at=T0 + timedelta(seconds=20),
        output_path="/exports/chat-video/x.mp4",
        requested_by="hychou",
    )

    raw = p.dumps()

    assert json.loads(raw) == {
        "stage": "rendering",
        "expected_seconds": 41,
        "elapsed_seconds": 20,
        "started_at": "2026-09-19T03:10:00Z",
        "heartbeat_at": "2026-09-19T03:10:20Z",
        "output_path": "/exports/chat-video/x.mp4",
        "requested_by": "hychou",
        "error": "",
        "token": "",
    }
    assert b"\n" in raw  # indented: a person opens this in the file tree
    assert Progress.loads(raw) == p


def test_a_progress_file_that_is_not_ours_is_refused_not_a_traceback():
    """A hand-edited or foreign file at the progress path: the caller treats
    it as absent (and may replace it), so the error is one `ValueError`."""
    with pytest.raises(ValueError):
        Progress.loads(b"not json")
    with pytest.raises(ValueError):
        Progress.loads(b'{"stage": 3}')


@pytest.mark.parametrize(
    ("stage", "heartbeat_age", "queued_alive", "alive"),
    [
        # queued: no heartbeat to judge — nobody rewrites the file while the
        # job waits in line — so the queue's own answer decides, at any age.
        ("queued", 0, True, True),
        ("queued", 3600, True, True),
        ("queued", 0, False, False),
        ("rendering", 59, False, True),
        ("rendering", 60, False, True),  # at the limit: still the worker's
        ("rendering", 61, False, False),  # past it: the worker died, replace
        ("encoding", 3600, True, False),  # a running stage never borrows the queue's answer
        ("failed", 3600, True, False),  # a failed run holds no claim
        ("failed", 0, True, False),
    ],
)
def test_alive_means_the_queue_still_holds_it_or_a_heartbeat_within_the_limit(
    stage, heartbeat_age, queued_alive, alive
):
    """The in-flight rule (409) and the stale rule (replace) are one
    predicate. A `queued` file is alive while the queue holds its job (the
    coordinator asks its rows); a running stage is alive while the worker
    breathed within `stale_after_seconds`; a failed one never."""
    p = Progress(
        stage=stage,
        expected_seconds=41,
        started_at=T0,
        heartbeat_at=T0,
        output_path="/x.mp4",
        requested_by="u",
    )

    assert (
        is_alive(
            p,
            now=T0 + timedelta(seconds=heartbeat_age),
            stale_after_seconds=60,
            queued_alive=queued_alive,
        )
        is alive
    )
