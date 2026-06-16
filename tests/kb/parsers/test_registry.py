"""ParserRegistry — first-match-wins dispatch.

The Ingestor holds ONE ``ParserRegistry`` populated at startup. Each
``pick(filename, mime, source) -> IParser | None`` call walks the
parsers in registration order; the first whose ``matches(...)``
returns ``True`` is the parser the Ingestor uses for this upload.
Returns ``None`` when no parser handles the file — the Ingestor falls
back to the current behaviour (text-mimes go through the chunker, the
rest are skipped or stored without indexing).

Test choices:

  - Parsers are matched by an injected predicate, not by importing
    real bundled parsers — these tests pin the REGISTRY shape, not
    any individual parser's matching rules.
  - Order matters: a ``register`` call appends to the tail. The same
    parser instance can register more than once (rare but legal); the
    earliest registration is the one ``pick`` returns.
"""

from __future__ import annotations

from collections.abc import Callable

from workspace_app.kb.parsers import IParser, IParserInput, MaterialisedParserInput, ParserRegistry


class _ConstParser(IParser):
    """Test parser that delegates ``matches`` to an injected predicate
    and ``parse`` to a constant return."""

    def __init__(
        self,
        *,
        label: str,
        matcher: Callable[[str, str], bool],
        documents: list[object] | None = None,
    ) -> None:
        self.label = label
        self._matcher = matcher
        self._docs = documents or []

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        return self._matcher(filename, mime)

    def parse(
        self, source: IParserInput, *, filename: str, mime: str, on_progress=None, on_preview=None
    ):  # type: ignore[no-untyped-def]
        return self._docs


def _input(data: bytes = b"") -> IParserInput:
    return MaterialisedParserInput(data)


def test_empty_registry_returns_none():
    r = ParserRegistry()
    assert r.pick(filename="x.csv", mime="text/csv", source=_input()) is None


def test_first_registered_parser_that_matches_wins():
    """Registration order is significant — operators who register a
    custom CSV parser at the head intentionally shadow the bundled
    one (e.g. an in-house dialect with weird quoting)."""
    r = ParserRegistry()
    a = _ConstParser(label="custom-csv", matcher=lambda f, m: f.endswith(".csv"))
    b = _ConstParser(label="bundled-csv", matcher=lambda f, m: f.endswith(".csv"))
    r.register(a)
    r.register(b)
    picked = r.pick(filename="d.csv", mime="text/csv", source=_input())
    assert picked is a


def test_pick_falls_through_to_next_parser_when_first_does_not_match():
    r = ParserRegistry()
    csv = _ConstParser(label="csv", matcher=lambda f, m: f.endswith(".csv"))
    json_ = _ConstParser(label="json", matcher=lambda f, m: f.endswith(".json"))
    r.register(csv)
    r.register(json_)
    assert r.pick(filename="d.json", mime="application/json", source=_input()) is json_


def test_pick_returns_none_when_no_parser_matches():
    r = ParserRegistry()
    r.register(_ConstParser(label="csv", matcher=lambda f, m: f.endswith(".csv")))
    assert r.pick(filename="d.xyz", mime="application/octet-stream", source=_input()) is None


def test_parsers_lists_registrations_in_order():
    """A small reflective accessor for diagnostics / logging — the
    operator can see what's wired at startup."""
    r = ParserRegistry()
    a = _ConstParser(label="a", matcher=lambda f, m: False)
    b = _ConstParser(label="b", matcher=lambda f, m: False)
    r.register(a)
    r.register(b)
    assert r.parsers() == [a, b]


def test_register_returns_the_registry_for_chaining():
    """Constructive-style registration: `ParserRegistry().register(a).register(b)`
    reads naturally for the bundled-parser-list factory."""
    r = ParserRegistry()
    a = _ConstParser(label="a", matcher=lambda f, m: True)
    assert r.register(a) is r


def test_all_matching_returns_every_parser_whose_matches_is_true():
    """The Ingestor runs ALL matching parsers (Q8 grilling): a PNG can
    feed an OCR parser AND a VLM-caption parser at the same ingest,
    each producing its own chunk packet tagged with its parser_id."""
    r = ParserRegistry()
    ocr = _ConstParser(label="ocr", matcher=lambda f, m: f.endswith(".png"))
    vlm = _ConstParser(label="vlm", matcher=lambda f, m: m.startswith("image/"))
    txt = _ConstParser(label="txt", matcher=lambda f, m: m == "text/plain")
    r.register(ocr)
    r.register(vlm)
    r.register(txt)
    picks = r.all_matching(filename="d.png", mime="image/png", source=_input())
    assert picks == [ocr, vlm]


def test_all_matching_returns_empty_list_when_nothing_matches():
    r = ParserRegistry()
    r.register(_ConstParser(label="csv", matcher=lambda f, m: f.endswith(".csv")))
    assert r.all_matching(filename="x.png", mime="image/png", source=_input()) == []


def test_pick_is_a_first_match_shortcut_over_all_matching():
    """`pick` stays as syntactic sugar — same registration-order
    semantics as before. Used by debug paths inspecting which parser
    would have claimed a file."""
    r = ParserRegistry()
    a = _ConstParser(label="a", matcher=lambda f, m: True)
    b = _ConstParser(label="b", matcher=lambda f, m: True)
    r.register(a)
    r.register(b)
    assert r.pick(filename="x", mime="text/plain", source=_input()) is a
