"""What a rendering is asked to look like.

One struct, three readers that must agree: the CLI's flags map onto it
one-for-one today; the job payload a worker pod will consume carries it
verbatim; the form behind a front-end button will edit its fields. Keeping it
a ``msgspec.Struct`` means all three validate the same way.
"""

from __future__ import annotations

import msgspec

Format = str  # "gif" | "mp4" | "webm"


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
    """Ceiling on the whole animation. A transcript that would run longer is
    compressed uniformly to fit — a 200-message chat must not become a
    twenty-minute job on a worker."""

    # ── what is shown ──────────────────────────────────────────────────
    tool_output_chars: int = 600
    """A tool's output beyond this is cut with an ellipsis."""
    max_asset_bytes: int = 4_000_000
    """An image a tool showed is inlined into the page up to this size;
    bigger ones become a file card. The page carries its pictures (it fetches
    nothing), and a 40 MB page is a slow, memory-hungry recording."""
    fmt: tuple[Format, ...] = ("gif",)
