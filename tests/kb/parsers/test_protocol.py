"""IParser + IParserInput abstract contracts.

The ABCs document the framework's promise to two audiences:

  - Custom parser authors (operators / in-house engineers writing an
    `IParser` subclass — issue #39): they need to know which methods
    to override and what shape `parse()` returns.
  - The Ingestor (`registry.pick(...)` → `parser.parse(source, ...)`):
    it relies on the contract to dispatch uniformly without per-type
    if/elif.

These tests pin the contract before any concrete parser exists, so a
later refactor that loosens the ABC raises loudly here instead of
silently breaking a downstream parser.
"""

from __future__ import annotations

from abc import ABC

import pytest

from workspace_app.kb.parsers.protocol import IParser, IParserInput


def test_iparser_inherits_from_abc():
    assert ABC in IParser.__mro__


def test_iparserinput_inherits_from_abc():
    assert ABC in IParserInput.__mro__


def test_iparser_requires_parse_and_matches():
    """A subclass that forgets EITHER parse() or matches() cannot be
    instantiated — caught at construction time, not at first use."""

    class MissingMatches(IParser):
        def parse(self, source, *, filename, mime, on_progress=None, on_preview=None):  # type: ignore[no-untyped-def]
            return []

    class MissingParse(IParser):
        def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
            return False

    with pytest.raises(TypeError, match="abstract"):
        MissingMatches()  # type: ignore[abstract]
    with pytest.raises(TypeError, match="abstract"):
        MissingParse()  # type: ignore[abstract]


def test_iparserinput_requires_three_input_forms():
    """as_bytes / as_path / as_stream all abstract — every concrete
    `IParserInput` provides all three so a parser can choose without
    capability-detecting at runtime."""

    class IncompleteOnlyBytes(IParserInput):
        def as_bytes(self) -> bytes:
            return b""

    with pytest.raises(TypeError, match="abstract"):
        IncompleteOnlyBytes()  # type: ignore[abstract]


def test_a_complete_iparser_instance_can_be_constructed():
    """Concrete subclass overriding both abstract methods constructs."""

    class HelloParser(IParser):
        def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
            return filename.endswith(".hello")

        def parse(self, source, *, filename, mime, on_progress=None, on_preview=None):  # type: ignore[no-untyped-def]
            return []

    p = HelloParser()
    assert isinstance(p, IParser)
