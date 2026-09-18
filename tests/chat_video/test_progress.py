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
    ("stage", "heartbeat_age", "alive"),
    [
        ("queued", 0, True),
        ("rendering", 59, True),
        ("rendering", 60, True),  # at the limit: still the worker's
        ("rendering", 61, False),  # past it: the worker died, replace
        ("encoding", 3600, False),
        ("failed", 3600, False),  # a failed run holds no claim
        ("failed", 0, False),
    ],
)
def test_alive_means_a_heartbeat_within_the_limit_and_not_finished(stage, heartbeat_age, alive):
    """The in-flight rule (409) and the stale rule (replace) are one
    predicate: alive ⇔ the stage is still running AND the worker breathed
    within `stale_after_seconds`. `queued` counts from when it was queued —
    the API wrote that heartbeat itself."""
    p = Progress(
        stage=stage,
        expected_seconds=41,
        started_at=T0,
        heartbeat_at=T0,
        output_path="/x.mp4",
        requested_by="u",
    )

    assert is_alive(p, now=T0 + timedelta(seconds=heartbeat_age), stale_after_seconds=60) is alive
