"""`VideoOptions` validates itself, so its three readers — CLI flags, a job
payload, a form — refuse the same nonsense the same way, up front."""

from __future__ import annotations

import re

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
        ("type_ms", 10**6, "type_ms"),
        ("stream_ms", 10**6, "stream_ms"),
        ("speed", 0.001, "speed"),
        ("speed", 1000, "speed"),
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


def test_an_odd_side_is_accepted_as_it_always_was():
    """A P7 rule refused odd sides "for the mp4 encoder"; the regression
    review ran master: `--width 1001 --height 601` produced a 1000×600 mp4,
    because Playwright's recorder rounds the frame before x264 sees it. The
    rule refused inputs that worked (every format, even `--html`) for a
    reason that does not happen on this stack. The form still offers even
    sizes so what it shows is what comes out."""
    assert VideoOptions(width=1001, height=601).width == 1001


def test_the_defaults_are_valid():
    assert VideoOptions() == msgspec.json.decode(b"{}", type=VideoOptions)


# ─── the server's ceilings (plan-chat-video-export decision 10) ──────────────


@pytest.mark.parametrize(
    ("width", "height", "max_seconds"),
    [(1920, 1080, 180), (1080, 1920, 180), (1280, 720, 1), (640, 360, 90)],
)
def test_options_inside_the_ceilings_pass(width, height, max_seconds):
    from workspace_app.chat_video.options import check_limits

    check_limits(
        VideoOptions(width=width, height=height, max_seconds=max_seconds),
        max_pixels=1920 * 1080,
        max_seconds=180,
        max_output_bytes=100_000_000,
    )


@pytest.mark.parametrize(
    ("width", "height", "max_seconds", "why"),
    [
        (1922, 1080, 180, "1922×1080 is 2,075,760 pixels; at most 2,073,600 (1920×1080)"),
        (3840, 2160, 90, "3840×2160 is 8,294,400 pixels; at most 2,073,600 (1920×1080)"),
        (1280, 720, 181, "max_seconds 181; at most 180"),
    ],
)
def test_options_past_a_ceiling_are_refused_with_the_ceiling_named(width, height, max_seconds, why):
    """The form never offers these; a caller of the API can send anything,
    so the server says which ceiling and what it is — one sentence."""
    from workspace_app.chat_video.options import check_limits

    with pytest.raises(ValueError, match=re.escape(why)):
        check_limits(
            VideoOptions(width=width, height=height, max_seconds=max_seconds),
            max_pixels=1920 * 1080,
            max_seconds=180,
            max_output_bytes=100_000_000,
        )


@pytest.mark.parametrize(
    ("per_file", "total", "fitted"),
    [
        (4_000_000, 10**12, (4_000_000, 100_000_000)),  # a script's 10**12: the ceiling
        (60_000_000, 50_000_000, (50_000_000, 50_000_000)),  # per-file under the total
        (4_000_000, 24_000_000, (4_000_000, 20_000_000)),  # the DEFAULTS under a 20 MB ceiling
        (1_000_000, 2_000_000, (1_000_000, 2_000_000)),  # already inside: the same object
    ],
    ids=["script-total", "per-file-over-total", "defaults-under-small-ceiling", "inside"],
)
def test_the_asset_budgets_are_fitted_under_the_output_ceiling(per_file, total, fitted):
    """The struct accepts any non-negative budget; the deployment bounds
    it by `max_output_bytes` — the number it already states for one video's
    weight (a script used to send 10**12 and have the worker read a whole
    workspace file into a 2 GiB pod). Fitted, not refused: the budgets have
    defaults the dialog never sends, and the first version's 422 under a
    20 MB ceiling named a knob the person had no field for — every video
    export from the UI refused, on a value the example config invites."""
    from workspace_app.chat_video.options import fit_asset_budgets

    before = VideoOptions(max_asset_bytes=per_file, max_assets_total_bytes=total)
    ceiling = (
        20_000_000 if total == 24_000_000 else (50_000_000 if total == 50_000_000 else 100_000_000)
    )

    after = fit_asset_budgets(before, max_output_bytes=ceiling)

    assert (after.max_asset_bytes, after.max_assets_total_bytes) == fitted
    if fitted == (per_file, total):
        assert after is before


def test_a_pixel_ceiling_that_is_no_whole_16_9_frame_is_named_by_the_number_alone():
    from workspace_app.chat_video.options import check_limits

    with pytest.raises(
        ValueError, match=re.escape("1280×1024 is 1,310,720 pixels; at most 1,000,000")
    ):
        check_limits(
            VideoOptions(width=1280, height=1024),
            max_pixels=1_000_000,
            max_seconds=180,
            max_output_bytes=100_000_000,
        )
