"""VLM capability probe — read rendered text off a generated test image
and assert the transcription contains it. This exercises the exact
VlmDescriber code path the image / PDF / slide parsers use, probing the
capability ingestion actually relies on: text + structure extraction.

Why not a colour probe: live finding (2026-06-06) — qwen2.5vl:7b via
Ollama reads text-bearing images reliably but hallucinates freely on
featureless synthetic ones (and llama.cpp's vision path can even
GGML-assert-crash on some small synthetic sizes). A solid-colour probe
therefore failed models that handle real documents fine. Text-bearing
probes match production input (screenshots, slides, scans).
"""

from __future__ import annotations

import io

from ...kb.vlm import IVlm, VlmDescriber
from ..protocol import CheckResult, ISanityCheck

# Distinctive, domain-flavoured probe text — unlikely to appear in a
# hallucination, trivially asserted case-insensitively.
_PROBE_LINES = ("REFLOW ZONE 3", "setpoint 245C")
_PROBE_KEYWORD = "reflow"


def _probe_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 96), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((12, 20), _PROBE_LINES[0], fill=(0, 0, 0))
    draw.text((12, 52), _PROBE_LINES[1], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class VlmDescribeCheck(ISanityCheck):
    check_id = "vlm-describe"
    description = "Read text from a known test image"

    def __init__(self, vlm: IVlm | None) -> None:
        self._vlm = vlm

    def run(self) -> CheckResult:
        if self._vlm is None:
            return CheckResult(check_id=self.check_id, status="skip", detail="not configured")
        text = VlmDescriber(self._vlm).describe(
            _probe_png(), "image/png", context="an uploaded image probe.png"
        )
        if _PROBE_KEYWORD in text.lower():
            return CheckResult(check_id=self.check_id, status="pass", detail="probe text read")
        return CheckResult(
            check_id=self.check_id,
            status="fail",
            detail="the model could not read the test image's text — image / PDF "
            f"visual ingestion would produce junk (got: {text[:120]!r})",
        )
