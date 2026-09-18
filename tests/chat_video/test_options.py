"""`VideoOptions` validates itself, so its three readers — CLI flags, a job
payload, a form — refuse the same nonsense the same way, up front."""

from __future__ import annotations

import msgspec
import pytest

from workspace_app.chat_video.options import VideoOptions


@pytest.mark.parametrize(
    ("field", "value", "word"),
    [
        ("speed", 0, "speed"),
        ("speed", -2, "speed"),
        ("width", 0, "width"),
        ("height", 10_000, "height"),
        ("zoom", 0.5, "zoom"),
        ("max_seconds", 0, "max_seconds"),
        ("fmt", ("exe",), "fmt"),
        ("fmt", (), "fmt"),
        ("type_ms", -1, "type_ms"),
        ("tool_output_chars", 0, "tool_output_chars"),
        ("zoom_ms", 10**10, "zoom_ms"),
        ("tool_pause_ms", 3 * 10**9, "tool_pause_ms"),
        ("max_seconds", 10**9, "max_seconds"),
        ("max_assets_total_bytes", -1, "max_assets_total_bytes"),
    ],
)
def test_nonsense_is_refused_with_the_field_named(field: str, value: object, word: str):
    with pytest.raises(ValueError, match=word):
        VideoOptions(**{field: value})  # ty: ignore[invalid-argument-type]


def test_a_job_payload_is_refused_at_decode_not_after_the_recording():
    """The same struct decoded from JSON — what a queued job carries — fails
    at decode time. `fmt: ["exe"]` used to be found by ffmpeg after a whole
    recording had run."""
    with pytest.raises(msgspec.ValidationError, match="fmt"):
        msgspec.json.decode(b'{"fmt": ["exe"]}', type=VideoOptions)
    with pytest.raises(msgspec.ValidationError, match="speed"):
        msgspec.json.decode(b'{"speed": 0}', type=VideoOptions)


def test_the_defaults_are_valid():
    assert VideoOptions() == msgspec.json.decode(b"{}", type=VideoOptions)
