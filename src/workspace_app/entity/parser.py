"""Parse an entity file into a renderable projection + diagnostics (#419 §E).

The parser NEVER raises on bad content: it returns whatever it could render
plus a list of diagnostics, so one broken entity degrades to a single
warning/row rather than killing the app. Broken frontmatter → the whole file
falls back to `body` and the entity drops out of any structured projection.
"""

from __future__ import annotations

from typing import Any

import msgspec
import yaml

from .diagnostics import Diagnostic
from .schema import EntitySchema, Role

__all__ = ["Diagnostic", "ParsedEntity", "parse_entity", "serialize_entity"]


class ParsedEntity(msgspec.Struct):
    number: int
    type_name: str
    fields: dict[str, Any]
    body: str
    diagnostics: list[Diagnostic]

    @property
    def ok(self) -> bool:
        """True when no `error`-level diagnostic dropped it from projection."""
        return not any(d.level == "error" for d in self.diagnostics)


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return `(frontmatter_text, body)`; `(None, whole)` when there's no
    well-formed `---` … `---` block."""
    if not text.startswith("---"):
        return None, text
    # Strip only the single newline after the opening fence — `lstrip("\n")`
    # would eat the blank line of an *empty* frontmatter and mistake the closing
    # fence for content.
    rest = text[3:]
    rest = rest[1:] if rest.startswith("\n") else rest
    end = rest.find("\n---")
    if end == -1:
        return None, text
    return rest[:end], rest[end + 4 :].lstrip("\n")


def _lint(fields: dict[str, Any], schema: EntitySchema) -> list[Diagnostic]:
    """Validate-but-don't-block (§C7): a value outside a closed vocabulary is a
    warning, not an error — it still projects."""
    out: list[Diagnostic] = []
    for spec in schema.fields:
        if spec.role is Role.STATUS and spec.values is not None:
            value = fields.get(spec.name)
            if value is not None and value not in spec.values:
                out.append(
                    Diagnostic("warning", f"{spec.name}={value!r} not in {spec.values}", spec.name)
                )
    return out


def parse_entity(raw: bytes, number: int, type_name: str, schema: EntitySchema) -> ParsedEntity:
    text = raw.decode("utf-8", errors="replace")
    diagnostics: list[Diagnostic] = []
    front, body = _split_frontmatter(text)
    if front is None:
        diagnostics.append(Diagnostic("error", "no frontmatter — shown as body only"))
        return ParsedEntity(number, type_name, {}, text, diagnostics)
    try:
        loaded = yaml.safe_load(front)
    except yaml.YAMLError as e:
        diagnostics.append(Diagnostic("error", f"malformed frontmatter YAML: {e}"))
        return ParsedEntity(number, type_name, {}, text, diagnostics)
    if loaded is not None and not isinstance(loaded, dict):
        diagnostics.append(Diagnostic("error", "frontmatter is not a mapping"))
        return ParsedEntity(number, type_name, {}, text, diagnostics)
    fields = {str(k): v for k, v in (loaded or {}).items()}
    diagnostics.extend(_lint(fields, schema))
    return ParsedEntity(number, type_name, fields, body, diagnostics)


def serialize_entity(fields: dict[str, Any], body: str) -> str:
    """Inverse of `parse_entity` for the structured write path — frontmatter
    from `fields` + the preserved `body`. Field insertion order is kept."""
    front = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}"
