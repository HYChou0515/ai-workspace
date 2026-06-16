"""ParserRegistry — runtime directory of ``IParser`` instances.

The Ingestor holds ONE ``ParserRegistry`` (populated at
``create_app`` time with bundled parsers + any custom parsers wired
through ``factories.get_parser_registry(settings)``). Per upload, the
Ingestor calls ``registry.pick(filename, mime, source)`` to choose
which parser owns this file; the first parser registered whose
``matches(...)`` returns ``True`` is the one picked.

First-match-wins is deliberate:

  - Operators registering an in-house parser at the head intentionally
    SHADOW a bundled parser for the same extension (e.g. a custom
    in-house CSV dialect with weird quoting). They opt in by ordering
    the registration.
  - The bundled parsers are appended at the tail by the framework, so
    the operator's wiring chooses precedence.

``None`` from ``pick`` means "no parser handles this file" — the
Ingestor falls back to its current text-mime + reader_for path until
those migrate too (issue #39 open follow-ups).
"""

from __future__ import annotations

from .protocol import IParser, IParserInput


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[IParser] = []

    def register(self, parser: IParser) -> ParserRegistry:
        """Append ``parser`` at the tail. Returns the registry so the
        wiring factory can chain registrations
        (``ParserRegistry().register(a).register(b)``)."""
        self._parsers.append(parser)
        return self

    def all_matching(self, *, filename: str, mime: str, source: IParserInput) -> list[IParser]:
        """Every registered parser whose ``matches(...)`` returns True
        for this upload, in registration order. The Ingestor runs each
        one — same blob, independent chunk packets — so e.g. a PNG can
        feed an OCR parser AND a VLM-caption parser at the same
        ingest, both writing chunks tagged with their own
        ``parser_id``. Empty list when nothing handles this upload."""
        return [p for p in self._parsers if p.matches(filename=filename, mime=mime, source=source)]

    def pick(self, *, filename: str, mime: str, source: IParserInput) -> IParser | None:
        """First matching parser, or ``None``. Convenience wrapper over
        ``all_matching`` for callers that genuinely only want one (e.g.
        a debug endpoint inspecting which parser would claim a file)."""
        matches = self.all_matching(filename=filename, mime=mime, source=source)
        return matches[0] if matches else None

    def parsers(self) -> list[IParser]:
        """A copy of the registered parsers, in registration order —
        for diagnostics / startup logging. Mutating the returned list
        doesn't affect the registry."""
        return list(self._parsers)
