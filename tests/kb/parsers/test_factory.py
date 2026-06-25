"""``factories.get_parser_registry(settings)`` — wires bundled +
custom parsers into a ``ParserRegistry``.

Operator config:

    kb:
      parsers:
        - my.pkg.MyCsvParser    # custom — head of registry
        - my.pkg.MyJsonParser   # head of registry

Order matters per Q4 grilling: custom registered FIRST so they shadow
bundled parsers for the same extension when intended. Bundled
parsers (PDF/HTML/DOCX) appended after, in fixed order.

Tests:
  - Empty `kb.parsers` → bundled-only registry.
  - Custom parsers prepended in the order operator declared.
  - Unknown dotted path raises at startup with a useful message.
  - Non-IParser target raises (the operator made a typo and pointed
    at the wrong class).
"""

from __future__ import annotations

import dataclasses

import pytest

from workspace_app.config.schema import Settings
from workspace_app.factories import get_parser_registry
from workspace_app.kb.parsers import IParser, IParserInput
from workspace_app.kb.parsers.chat_export_parser import ChatExportParser
from workspace_app.kb.parsers.json_file import JsonParser
from workspace_app.kb.parsers.llamaindex_readers import DocxParser, HtmlParser
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.parsers.slides import PptxParser
from workspace_app.kb.parsers.svg_image import SvgParser
from workspace_app.kb.parsers.tabular import CsvParser, ExcelParser
from workspace_app.kb.parsers.vlm_image import VlmImageParser

_BUNDLED = [
    PdfParser,
    HtmlParser,
    DocxParser,
    ChatExportParser,
    JsonParser,
    CsvParser,
    ExcelParser,
    VlmImageParser,
    SvgParser,
    PptxParser,
]

# A test custom parser exposed at module level so the dotted-path
# resolver in `factories.get_parser_registry` can import it. Lives
# inside the test module — `tests.kb.parsers.test_factory.HelloParser`.


class HelloParser(IParser):
    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        return filename.endswith(".hello")

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress=None,
        on_preview=None,
        unit_range=None,
    ):  # type: ignore[no-untyped-def]
        return []


class NotAParser:
    """Intentionally NOT an IParser — for the "operator typo'd the
    dotted path" test."""


def test_empty_kb_parsers_returns_bundled_only_registry():
    s = Settings()
    reg = get_parser_registry(s)
    types = [type(p) for p in reg.parsers()]
    assert types == _BUNDLED


def test_custom_parsers_register_at_the_head_in_declared_order():
    """Custom parsers shadow bundled ones for the same extension —
    operator declares them FIRST in kb.parsers and the registry
    iterates registration order."""
    base = Settings()
    s = dataclasses.replace(
        base,
        kb=dataclasses.replace(
            base.kb,
            parsers=["tests.kb.parsers.test_factory.HelloParser"],
        ),
    )
    reg = get_parser_registry(s)
    types = [type(p) for p in reg.parsers()]
    assert types == [HelloParser, *_BUNDLED]


def test_unknown_dotted_path_raises_at_startup_with_the_path_in_the_message():
    base = Settings()
    s = dataclasses.replace(
        base,
        kb=dataclasses.replace(
            base.kb,
            parsers=["totally.made.up.NopeParser"],
        ),
    )
    with pytest.raises(ValueError, match="totally.made.up.NopeParser"):
        get_parser_registry(s)


def test_non_iparser_target_raises_with_a_message_naming_the_class():
    """`NotAParser` exists at the named path but isn't an `IParser`
    subclass — fail loud at startup so the operator notices the typo
    before the first upload hits the broken parser."""
    base = Settings()
    s = dataclasses.replace(
        base,
        kb=dataclasses.replace(
            base.kb,
            parsers=["tests.kb.parsers.test_factory.NotAParser"],
        ),
    )
    with pytest.raises(TypeError, match="NotAParser"):
        get_parser_registry(s)


def test_parsers_disabled_skips_bundled_by_class_name():
    """Issue #39 Docling adaptation point: with all-matching dispatch a
    custom parser RUNS ALONGSIDE the bundled one rather than shadowing
    it, so replacement = register custom + disable bundled by class
    name. Unknown names raise so a typo doesn't silently leave the
    bundled parser running."""
    base = Settings()
    s = dataclasses.replace(
        base,
        kb=dataclasses.replace(base.kb, parsers_disabled=["PdfParser", "ExcelParser"]),
    )
    reg = get_parser_registry(s)
    types = [type(p) for p in reg.parsers()]
    assert types == [
        HtmlParser,
        DocxParser,
        ChatExportParser,
        JsonParser,
        CsvParser,
        VlmImageParser,
        SvgParser,
        PptxParser,
    ]


def test_parsers_disabled_unknown_name_raises():
    base = Settings()
    s = dataclasses.replace(
        base,
        kb=dataclasses.replace(base.kb, parsers_disabled=["NoSuchParser"]),
    )
    with pytest.raises(ValueError, match="NoSuchParser"):
        get_parser_registry(s)


def test_vlm_describer_injection_follows_kb_vlm_llm():
    """Default settings wire a VlmDescriber (bundled kb-vlm preset) into
    the vision-capable parsers — VlmImageParser then matches images.
    With `kb.vlm_llm: null` it gets None and stops matching, so image
    uploads store chunk-less (Q9b) instead of erroring."""
    from workspace_app.kb.parsers.input import MaterialisedParserInput

    src = MaterialisedParserInput(b"\x89PNG", filename="d.png")

    reg = get_parser_registry(Settings())
    image_parsers = [p for p in reg.parsers() if isinstance(p, VlmImageParser)]
    assert image_parsers and image_parsers[0].matches(
        filename="d.png", mime="image/png", source=src
    )

    s = dataclasses.replace(Settings(), kb=dataclasses.replace(Settings().kb, vlm_llm=None))
    reg_off = get_parser_registry(s)
    image_off = [p for p in reg_off.parsers() if isinstance(p, VlmImageParser)]
    assert image_off and not image_off[0].matches(filename="d.png", mime="image/png", source=src)


def test_kb_parsers_entry_without_dots_raises():
    """`kb.parsers: ["NopeParser"]` (no module path) is a config typo —
    raise at startup naming the entry."""
    base = Settings()
    s = dataclasses.replace(
        base,
        kb=dataclasses.replace(base.kb, parsers=["NopeParser"]),
    )
    with pytest.raises(ValueError, match="NopeParser"):
        get_parser_registry(s)


def test_kb_parsers_module_without_the_attribute_raises():
    """Module imports fine but the class name doesn't exist in it."""
    base = Settings()
    s = dataclasses.replace(
        base,
        kb=dataclasses.replace(base.kb, parsers=["tests.kb.parsers.test_factory.Missing"]),
    )
    with pytest.raises(ValueError, match="Missing"):
        get_parser_registry(s)
