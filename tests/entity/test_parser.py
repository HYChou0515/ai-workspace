"""The parser never raises — every malformed shape degrades to `body` + an
error diagnostic (#419 §E), so one bad entity can't kill the projection."""

from __future__ import annotations

from workspace_app.entity.parser import parse_entity
from workspace_app.entity.schema import EntitySchema

_EMPTY = EntitySchema(fields=[])


def test_malformed_yaml_frontmatter_degrades_to_body() -> None:
    parsed = parse_entity(b"---\nfoo: [unclosed\n---\n\nbody", 1, "issue", _EMPTY)
    assert not parsed.ok
    assert any("malformed" in d.message for d in parsed.diagnostics)


def test_non_mapping_frontmatter_is_an_error() -> None:
    parsed = parse_entity(b"---\njust a scalar\n---\n\nbody", 1, "issue", _EMPTY)
    assert not parsed.ok
    assert any("not a mapping" in d.message for d in parsed.diagnostics)


def test_unclosed_frontmatter_is_shown_as_body() -> None:
    parsed = parse_entity(b"---\nno closing fence here", 1, "issue", _EMPTY)
    assert not parsed.ok
    assert parsed.body == "---\nno closing fence here"


def test_empty_frontmatter_parses_to_no_fields() -> None:
    parsed = parse_entity(b"---\n\n---\n\nbody", 1, "issue", _EMPTY)
    assert parsed.ok
    assert parsed.fields == {}
