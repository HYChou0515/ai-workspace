"""What a rendering is asked to look like.

One struct, three readers that must agree: the CLI's flags map onto it
one-for-one today; the job payload a worker pod will consume carries it
verbatim; the form behind a front-end button will edit its fields. Keeping it
a ``msgspec.Struct`` means all three validate the same way.
"""

from __future__ import annotations

import msgspec

Format = str  # "gif" | "mp4" | "webm"
FORMATS: tuple[Format, ...] = ("gif", "mp4", "webm")


class VideoOptions(msgspec.Struct, frozen=True):
    # ── the frame ──────────────────────────────────────────────────────
    width: int = 1280
    """Output pixels. The page is laid out at this size and the recording IS
    the viewport, so no scaling happens afterwards."""
    height: int = 720
    chat_width: int = 760
    """The chat column's width in CSS px (before ``scale``), centred in the
    frame."""
    scale: float = 0.0
    """How much the whole UI is enlarged. ``0`` = automatic: the frame's size
    relative to 1280×720, never below 1 — so 1080p renders at 1.5× and 4K at
    3× rather than as a small page with black around it."""

    # ── the camera ─────────────────────────────────────────────────────
    zoom: float = 1.8
    """How far the camera pushes in on the composer for a user turn. ``1``
    disables the push. The player caps it so the whole composer stays in
    frame whatever the width."""
    zoom_ms: int = 900

    # ── the tempo ──────────────────────────────────────────────────────
    type_ms: int = 55
    """Per character while the user's message is typed."""
    stream_ms: int = 22
    """Per character while the assistant's reply streams."""
    tool_pause_ms: int = 1200
    """How long a tool card spins between "called" and "returned"."""
    speed: float = 1.0
    """Uniform multiplier on every delay; ``2`` plays twice as fast."""
    max_seconds: int = 90
    """Ceiling on the animation as ASKED FOR: a transcript that would run
    longer has every delay compressed uniformly to fit — a 200-message chat
    must not become a twenty-minute job on a worker. Soft by one term: the
    browser's own per-character cost is not a delay and does not compress,
    so a very long transcript still runs over (``Timeline.overhead_ms`` is
    that term; ``playback_ms`` the honest total)."""

    # ── what is shown ──────────────────────────────────────────────────
    tool_output_chars: int = 600
    """A tool's output beyond this is cut with an ellipsis."""
    max_asset_bytes: int = 4_000_000
    """An image a tool showed is inlined into the page up to this size;
    bigger ones become a file card. The page carries its pictures (it fetches
    nothing), and a 40 MB page is a slow, memory-hungry recording."""
    fmt: tuple[Format, ...] = ("gif",)

    def __post_init__(self) -> None:
        """Refuse nonsense here, once, for every reader — a flag, a decoded job
        payload (msgspec runs this on decode too) or a form — with the field
        named. Before this, ``speed=0`` was a ZeroDivisionError in the
        timeline and ``fmt=("exe",)`` was found by ffmpeg after the whole
        recording had run."""
        checks = (
            (16 <= self.width <= 7680, "width must be 16..7680"),
            (16 <= self.height <= 4320, "height must be 16..4320"),
            (self.chat_width >= 200, "chat_width must be at least 200"),
            (self.scale >= 0, "scale must be 0 (automatic) or positive"),
            (self.zoom >= 1, "zoom must be at least 1 (1 = no push-in)"),
            (self.zoom_ms >= 0, "zoom_ms must not be negative"),
            (self.type_ms >= 0, "type_ms must not be negative"),
            (self.stream_ms >= 0, "stream_ms must not be negative"),
            (self.tool_pause_ms >= 0, "tool_pause_ms must not be negative"),
            (self.speed > 0, "speed must be positive"),
            (self.max_seconds >= 1, "max_seconds must be at least 1"),
            (self.tool_output_chars >= 1, "tool_output_chars must be at least 1"),
            (self.max_asset_bytes >= 0, "max_asset_bytes must not be negative"),
            (bool(self.fmt), "fmt must name at least one format"),
            (all(f in FORMATS for f in self.fmt), f"fmt must be among {', '.join(FORMATS)}"),
        )
        for ok, why in checks:
            if not ok:
                raise ValueError(why)
