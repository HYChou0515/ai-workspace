"""SvgParser — vector SVG rasterized (cairosvg) then described by the VLM (#81).

SVG is XML, so the raster VlmImageParser excludes it; SvgParser rasterizes to
PNG and runs the same VLM describe path. No VLM wired → no match (0 chunks).
Rasterize / VLM failures degrade to 0 chunks rather than failing the upload.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence

import pytest

from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.svg_image import (
    _DEFAULT_CJK_FONT,
    _VLM_DEFAULT_SCALE,
    _VLM_MAX_SCALE,
    SvgParser,
    _cjk_font_family,
    _force_cjk_font,
    _render_scale,
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
    img_bytes, img_mime = images[0]  # ty: ignore[not-iterable]
    assert img_mime == "image/png"
    assert img_bytes[:4] == b"\x89PNG"
    assert any("diagram.svg" in m for m in progress)
    # Issue #115: the VLM emits Markdown → flag it so DispatchSplitter routes
    # the SVG description through the heading-aware Markdown path.
    assert docs[0].metadata["content_format"] == "markdown"
    assert docs[0].metadata["mime"] == "image/svg+xml"  # source mime stays honest


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_svg_parser_rasterizes_at_a_legible_resolution_for_ocr():
    # #185: cairosvg with no scale renders an SVG at its nominal px size, which
    # is too soft for the VLM to OCR. The long side is pulled up to the target.
    vlm, d = _describer()
    p = SvgParser(d)
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600">'
        b'<text x="20" y="60" font-size="40">Flow start</text></svg>'
    )
    list(p.parse(_input(svg, "big.svg"), filename="big.svg", mime="image/svg+xml"))
    (call,) = vlm.calls
    img_bytes, _ = call["images"][0]  # ty: ignore[not-iterable]
    w, _h = _png_size(img_bytes)
    assert 2000 <= w <= 2100  # nominal 1000px scaled ~2× toward the OCR target


def test_svg_parser_caps_the_upscale_for_a_tiny_svg():
    # A tiny icon-sized SVG is upscaled, but bounded by the scale cap so a
    # 20×20 source can't balloon into an enormous raster.
    vlm, d = _describer()
    p = SvgParser(d)
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
        b'<rect width="20" height="20"/></svg>'
    )
    list(p.parse(_input(svg, "tiny.svg"), filename="tiny.svg", mime="image/svg+xml"))
    img_bytes, _ = vlm.calls[0]["images"][0]  # ty: ignore[not-iterable]
    w, _h = _png_size(img_bytes)
    assert 20 < w <= 320  # upscaled past nominal, but capped well below the target


def test_render_scale_pulls_long_side_to_target_from_a_viewbox_only_svg():
    # viewBox but no width/height (mermaid / draw.io): the viewBox extent drives
    # the scale, since that's what cairosvg renders to.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 512"></svg>'
    assert _render_scale(svg) == pytest.approx(2.0)  # 2048 / 1024


def test_render_scale_caps_the_upscale_of_a_tiny_svg():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
    assert _render_scale(svg) == _VLM_MAX_SCALE  # 2048/10 clamped to the cap


def test_render_scale_floors_at_one_for_an_already_large_svg():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4000" height="3000"></svg>'
    assert _render_scale(svg) == 1.0  # never render softer than nominal


def test_render_scale_ignores_zero_and_unit_dimensions_and_uses_the_viewbox():
    # width/height of 0 or a non-px unit carry no intrinsic size → viewBox wins.
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"'
        b' width="0" height="0" viewBox="0 0 1024 512"></svg>'
    )
    assert _render_scale(svg) == pytest.approx(2.0)


def test_render_scale_defaults_when_the_intrinsic_size_is_unknown():
    # percentage sizes, non-numeric sizes, a malformed viewBox, and unparseable
    # XML all leave the size unknown → still bump resolution by the default.
    pct = b'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%"></svg>'
    auto = b'<svg xmlns="http://www.w3.org/2000/svg" width="auto" height="auto"></svg>'
    bad_vb = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 wide tall"></svg>'
    short_vb = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100"></svg>'
    assert _render_scale(pct) == _VLM_DEFAULT_SCALE
    assert _render_scale(auto) == _VLM_DEFAULT_SCALE
    assert _render_scale(bad_vb) == _VLM_DEFAULT_SCALE
    assert _render_scale(short_vb) == _VLM_DEFAULT_SCALE
    assert _render_scale(b"not svg <<<") == _VLM_DEFAULT_SCALE


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
    # parse() rasterizes at an OCR-legible scale (#185); render the comparison
    # PNGs at the same scale so the only difference under test is the CJK font.
    scale = _render_scale(_SVG_CJK)
    naive = cairosvg.svg2png(bytestring=_SVG_CJK, scale=scale)
    forced = cairosvg.svg2png(bytestring=_force_cjk_font(_SVG_CJK, fam), scale=scale)
    if naive == forced:
        pytest.skip(f"CJK font {fam!r} not installed; cannot prove the raster changed")

    vlm, d = _describer()
    p = SvgParser(d)
    list(p.parse(_input(_SVG_CJK, "cjk.svg"), filename="cjk.svg", mime="image/svg+xml"))
    (call,) = vlm.calls
    images = call["images"]
    assert isinstance(images, list)
    img_bytes, img_mime = images[0]  # ty: ignore[not-iterable]
    assert img_mime == "image/png"
    assert img_bytes == forced  # parse() applied the CJK-font override...
    assert img_bytes != naive  # ...so the VLM never sees the tofu render
