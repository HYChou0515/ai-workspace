"""SvgParser — vector SVG rasterized (cairosvg) then described by the VLM (#81).

SVG is XML, so the raster VlmImageParser excludes it; SvgParser rasterizes to
PNG and runs the same VLM describe path. No VLM wired → no match (0 chunks).
Rasterize / VLM failures degrade to 0 chunks rather than failing the upload.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence

import pytest

from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.svg_image import (
    _DEFAULT_CJK_FONT,
    SvgParser,
    _cjk_font_family,
    _force_cjk_font,
)
from workspace_app.kb.vlm import IVlm, VlmDescriber

_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="24">'
    b'<text x="2" y="16">Gate STI</text></svg>'
)

# Chinese label in a Latin-only font — without a fallback fix, cairosvg's
# toy-font API renders this as tofu (□) because DejaVu/Arial lack CJK glyphs (#85).
_SVG_CJK = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="40">'
    '<text x="6" y="26" font-family="Arial" font-size="20">驗證資料 ABC</text></svg>'
).encode()


class FakeVlm(IVlm):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.calls.append({"prompt": prompt, "images": list(images)})
        yield "## OCR\n\nGate STI diagram", False


def _describer() -> tuple[FakeVlm, VlmDescriber]:
    vlm = FakeVlm()
    return vlm, VlmDescriber(vlm)


def _input(data: bytes, filename: str) -> MaterialisedParserInput:
    return MaterialisedParserInput(data, filename=filename)


def test_svg_parser_does_not_match_without_a_vlm():
    p = SvgParser(None)
    assert p.matches(filename="d.svg", mime="image/svg+xml", source=_input(_SVG, "d.svg")) is False


def test_svg_parser_matches_svg_only():
    _, d = _describer()
    p = SvgParser(d)
    assert p.matches(filename="d.svg", mime="image/svg+xml", source=_input(_SVG, "d.svg")) is True
    assert p.matches(filename="noext", mime="image/svg+xml", source=_input(_SVG, "noext")) is True
    assert p.matches(filename="d.png", mime="image/png", source=_input(b"x", "d.png")) is False


def test_svg_parser_rasterizes_then_describes_via_vlm():
    vlm, d = _describer()
    p = SvgParser(d)
    progress: list[str] = []
    docs = list(
        p.parse(
            _input(_SVG, "diagram.svg"),
            filename="diagram.svg",
            mime="image/svg+xml",
            on_progress=progress.append,
        )
    )
    assert len(docs) == 1
    assert "Gate STI diagram" in docs[0].text
    # the VLM received a real rasterized PNG — NOT the raw SVG bytes
    (call,) = vlm.calls
    images = call["images"]
    assert isinstance(images, list) and len(images) == 1
    img_bytes, img_mime = images[0]
    assert img_mime == "image/png"
    assert img_bytes[:4] == b"\x89PNG"
    assert any("diagram.svg" in m for m in progress)


def test_svg_parser_degrades_to_zero_chunks_on_unrasterizable_input():
    _, d = _describer()
    p = SvgParser(d)
    docs = list(
        p.parse(
            _input(b"this is not svg <<<", "broken.svg"),
            filename="broken.svg",
            mime="image/svg+xml",
        )
    )
    assert docs == []  # bad SVG → graceful 0 chunks, not a crashed upload


def test_svg_parser_degrades_when_the_vlm_chokes():
    class _BoomVlm(IVlm):
        def stream(self, prompt: str, *, images: Sequence[tuple[bytes, str]]):
            raise RuntimeError("GGML crash on a featureless image")
            yield  # pragma: no cover — generator shape only

    p = SvgParser(VlmDescriber(_BoomVlm()))
    docs = list(p.parse(_input(_SVG, "d.svg"), filename="d.svg", mime="image/svg+xml"))
    assert docs == []


# --- #85: CJK rasterizes as tofu because cairosvg's toy-font API has no font
# fallback. Force an installed CJK-capable family onto every text run before
# rasterizing so the PNG the VLM reads carries real glyphs, not boxes. ---


def test_force_cjk_font_injects_an_important_style_naming_the_family():
    out = _force_cjk_font(_SVG_CJK, "Noto Sans CJK TC")
    root = ET.fromstring(out)
    styles = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "style"]
    assert styles, "no <style> was injected"
    css = styles[0].text or ""
    assert "Noto Sans CJK TC" in css
    assert "!important" in css  # must beat the element's own font-family="Arial"
    assert "text" in css  # the rule targets text runs
    assert "驗證資料 ABC" in out.decode("utf-8")  # original content preserved


def test_force_cjk_font_returns_the_input_unchanged_on_unparseable_xml():
    bad = b"this is not svg <<<"
    assert _force_cjk_font(bad, "Noto Sans CJK TC") == bad


def test_cjk_font_family_uses_fontconfigs_choice():
    import subprocess

    class _Done:
        stdout = "WenQuanYi Zen Hei\n"

    _cjk_font_family.cache_clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "run", lambda *a, **k: _Done())
        assert _cjk_font_family() == "WenQuanYi Zen Hei"
    _cjk_font_family.cache_clear()


def test_cjk_font_family_falls_back_when_fc_match_is_unavailable():
    import subprocess

    def _boom(*a, **k):
        raise OSError("no fc-match on this box")

    _cjk_font_family.cache_clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "run", _boom)
        assert _cjk_font_family() == _DEFAULT_CJK_FONT
    _cjk_font_family.cache_clear()


def test_svg_parser_feeds_the_vlm_a_cjk_forced_raster_not_tofu():
    import cairosvg

    fam = _cjk_font_family()
    naive = cairosvg.svg2png(bytestring=_SVG_CJK)
    forced = cairosvg.svg2png(bytestring=_force_cjk_font(_SVG_CJK, fam))
    if naive == forced:
        pytest.skip(f"CJK font {fam!r} not installed; cannot prove the raster changed")

    vlm, d = _describer()
    p = SvgParser(d)
    list(p.parse(_input(_SVG_CJK, "cjk.svg"), filename="cjk.svg", mime="image/svg+xml"))
    (call,) = vlm.calls
    images = call["images"]
    assert isinstance(images, list)
    img_bytes, img_mime = images[0]
    assert img_mime == "image/png"
    assert img_bytes == forced  # parse() applied the CJK-font override...
    assert img_bytes != naive  # ...so the VLM never sees the tofu render
