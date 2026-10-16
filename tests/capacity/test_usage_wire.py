"""The usage record's wire format (docs/plan-capacity-usage.md §5.1): one JSON
object per line, the envelope a collector routes on, and the interval the
numbers describe in `@timestamp`."""

import json

from workspace_app.capacity.usage_wire import document, line, window_start


def test_a_record_is_one_line_of_json_with_the_envelope_the_collector_routes_on():
    doc = document(
        "work",
        1_760_501_100_000,
        pod="api-7f9c",
        component="api",
        fields={"user": "alice", "work": "req:GET /api/x", "count": 3},
    )

    text = line(doc)

    assert "\n" not in text
    assert json.loads(text) == {
        "@timestamp": "2025-10-15T04:05:00.000Z",
        "event": {"dataset": "workspace.usage"},
        "usage": {
            "v": 1,
            "kind": "work",
            "pod": "api-7f9c",
            "component": "api",
            "user": "alice",
            "work": "req:GET /api/x",
            "count": 3,
        },
    }


def test_milliseconds_survive_into_the_timestamp():
    doc = document("turn", 1_760_501_100_042, pod="p", component="api", fields={})

    assert doc["@timestamp"] == "2025-10-15T04:05:00.042Z"


def test_a_field_the_source_could_not_read_is_left_out_not_written_as_null():
    """A kernel without PSI has no pressure files: the row must not claim 0
    (no pressure) or carry a null an ES mapping would have to accept."""
    doc = document(
        "sandbox", 0, pod="p", component="sandbox-host", fields={"cpu_s": 1.5, "cpu_psi_s": None}
    )

    assert doc["usage"]["cpu_s"] == 1.5
    assert "cpu_psi_s" not in doc["usage"]


def test_a_name_outside_ascii_cannot_be_split_by_a_pipeline_encoding():
    text = line(document("work", 0, pod="p", component="api", fields={"user": "周宏毅"}))

    assert text.isascii()
    assert json.loads(text)["usage"]["user"] == "周宏毅"


def test_windows_are_aligned_to_the_epoch_so_every_window_lands_in_one_bucket():
    """A 5-minute date histogram puts a window in exactly one bucket only when
    windows start on multiples of their length since the epoch."""
    assert window_start(1_760_501_100_000 + 299_999, 300) == 1_760_501_100_000
    assert window_start(1_760_501_100_000 + 300_000, 300) == 1_760_501_400_000
    assert window_start(1_760_501_100_000 + 59_000, 60) == 1_760_501_100_000
