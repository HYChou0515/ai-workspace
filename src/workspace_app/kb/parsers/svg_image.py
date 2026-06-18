"""SvgParser — vector SVG via rasterize-then-VLM (#81).

SVG is XML, not a raster image, so ``VlmImageParser`` excludes it. We rasterize
it to PNG (cairosvg) and run the SAME VLM describe path as raster images — the
layered prompt does verbatim OCR of the SVG's text labels AND describes the
diagram, so the chunk carries both.

cairosvg renders text through cairo's *toy* font API, which selects a single
font face per run and does **no per-glyph fallback**: a CJK label in a Latin
font (or the default ``sans-serif`` → DejaVu) rasterizes to tofu (□), so the VLM
then OCRs garbage even though converting the SVG to PNG elsewhere looks fine
(#85). Before rasterizing we therefore force an installed CJK-capable family
onto every text run via an injected ``!important`` ``<style>`` — fontconfig
(``fc-match``) tells us which family actually covers Chinese on this box.

No VLM wired → ``matches`` returns False, so an SVG stores with zero chunks
(same as raster images; a reindex picks it up once ``kb.vlm_llm`` is set).
Rasterize or VLM failures degrade to zero chunks rather than failing the upload:
cairosvg/libcairo may be absent, or a featureless rasterized SVG can make some
local VLMs hallucinate/crash (see reference_qwen25vl_ollama_quirks).
"""

from __future__ import annotations

import functools
import logging
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..vlm import VlmDescriber
from .protocol import IParser, IParserInput

if TYPE_CHECKING:
    from llama_index.core.schema import Document

_LOGGER = logging.getLogger(__name__)
_SVG_MIME = "image/svg+xml"
_SVG_NS = "http://www.w3.org/2000/svg"
# Best guess for Traditional Chinese when fontconfig can't be queried; harmless
# if absent (fontconfig substitutes; Latin still renders, CJK stays tofu — i.e.
# no CJK font on the box at all, which no rasterizer could fix anyway).
_DEFAULT_CJK_FONT = "Noto Sans CJK TC"

# Serialize the SVG namespace with no prefix so the injected ``text { … }`` CSS
# type selector matches (cssselect2 matches bare type selectors in the default
# namespace). Module-level + idempotent.
ET.register_namespace("", _SVG_NS)


@functools.cache
def _cjk_font_family() -> str:
    """The installed font family fontconfig picks for Traditional Chinese, or
    ``_DEFAULT_CJK_FONT`` when the ``fc-match`` CLI is unavailable. Memoized —
    the font set doesn't change over a process's life."""
    try:
        done = subprocess.run(
            ["fc-match", "-f", "%{family[0]}", ":lang=zh-tw"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _LOGGER.debug("fc-match unavailable, using default CJK font: %s", exc)
        return _DEFAULT_CJK_FONT
    return done.stdout.strip() or _DEFAULT_CJK_FONT


def _force_cjk_font(svg_bytes: bytes, family: str) -> bytes:
    """Inject an ``!important`` ``<style>`` forcing ``family`` onto every text
    run, so cairosvg renders CJK glyphs instead of tofu. ``!important`` beats
    both presentation attributes (``font-family="Arial"``) and inline styles.
    Unparseable XML → return the bytes untouched (let the caller's rasterize
    raise and degrade)."""
    try:
        root = ET.fromstring(svg_bytes)
    except ET.ParseError:
        return svg_bytes
    style = ET.Element(f"{{{_SVG_NS}}}style")
    safe = family.replace("'", "")  # family names with apostrophes are degenerate
    style.text = f"text,tspan,textPath,tref{{font-family:'{safe}' !important;}}"
    root.insert(0, style)
    return ET.tostring(root, encoding="utf-8")


class SvgParser(IParser):
    def __init__(self, describer: VlmDescriber | None) -> None:
        self._describer = describer

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        if self._describer is None:
            return False
        return mime == _SVG_MIME or filename.lower().endswith(".svg")

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
    ) -> list[Document]:
        from llama_index.core.schema import Document

        assert self._describer is not None  # matches() gates on it
        try:
            import cairosvg  # lazy: a deploy without libcairo still builds the registry

            svg = _force_cjk_font(source.as_bytes(), _cjk_font_family())
            png = cairosvg.svg2png(bytestring=svg)
        except Exception as exc:  # noqa: BLE001 — bad SVG / missing libcairo → 0 chunks, not error
            _LOGGER.warning("SvgParser: could not rasterize %s: %s", filename, exc)
            return []
        if on_progress is not None:
            on_progress(f"SvgParser: describing {filename}")
        try:
            text = self._describer.describe(
                png, "image/png", context=f"the uploaded SVG image {filename}"
            )
        except Exception as exc:  # noqa: BLE001 — VLM choke on a featureless SVG → 0 chunks
            _LOGGER.warning("SvgParser: VLM describe failed for %s: %s", filename, exc)
            return []
        # content_format flags the VLM Markdown for the heading-aware splitter
        # path (issue #115); source mime stays the original image/svg+xml.
        return [
            Document(
                text=text,
                metadata={"filename": filename, "mime": mime, "content_format": "markdown"},
            )
        ]
