"""Deterministic code outline (issue #281) — a faithful symbol skeleton of one
source file, extracted via tree-sitter (the same grammar pack the ingest
``CodeSplitter`` uses). It lists a file's top-level imports / functions /
classes in source order, so an L0 file card can pair this exact skeleton with a
short LLM one-liner — the prose can be wrong, but the skeleton never
hallucinates or silently drops a definition.

Unsupported extensions and parse failures degrade to ``""`` (the card still
gets its LLM one-liner from the file text), never raise — one weird file must
not wedge a whole-repo build.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

# Source-file extension → tree-sitter language name (the starter set, matching
# li_pipeline's CodeSplitter dispatch). Expand as more languages get demand.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
}


def _language_for(path: str) -> str | None:
    p = path.lower()
    for ext, lang in _LANG_BY_EXT.items():
        if p.endswith(ext):
            return lang
    return None


# Top-level definition node type → outline label, per grammar family. .tsx
# shares typescript's node types; .jsx already maps to "javascript" above.
_DEF_LABELS: dict[str, dict[str, str]] = {
    "python": {"function_definition": "def", "class_definition": "class"},
    "javascript": {"function_declaration": "function", "class_declaration": "class"},
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    },
}

# Wrapper nodes that hold the real definition in a child field — Python
# decorators (`definition`) and JS/TS `export …` (`declaration`).
_UNWRAP = frozenset({"decorated_definition", "export_statement"})

# Import-ish statements: emit their first source line verbatim.
_IMPORTS = frozenset({"import_statement", "import_from_statement"})


def _config_lang(lang: str) -> str:
    return "typescript" if lang == "tsx" else lang


def outline(path: str, text: str) -> str:
    """A newline-joined skeleton of ``text``'s top-level symbols, or ``""`` for
    a non-code extension / unparsable source."""
    lang = _language_for(path)
    if lang is None:
        return ""
    data = text.encode("utf-8")
    try:
        from tree_sitter_languages import get_parser

        tree = get_parser(lang).parse(data)
    except Exception:  # unknown grammar / native parse error — degrade, don't raise
        return ""
    labels = _DEF_LABELS[_config_lang(lang)]
    lines: list[str] = []
    for node in tree.root_node.children:
        line = _summarize(node, data, labels)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _node_text(node: Node, data: bytes) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _summarize(node: Node, data: bytes, labels: dict[str, str]) -> str | None:
    """One outline line for a top-level node, or None to skip it. ``labels``
    maps a definition node type to its display word for the active grammar."""
    t = node.type
    if t in _UNWRAP:
        # Python decorators expose the inner def via `definition`; JS/TS
        # `export …` via `declaration`.
        inner = node.child_by_field_name("definition") or node.child_by_field_name("declaration")
        return _summarize(inner, data, labels) if inner is not None else None
    if t in labels:
        name = node.child_by_field_name("name")
        if name is None:  # pragma: no cover — tree-sitter always names a real
            return None  # def/class; malformed code yields ERROR nodes instead
        return f"{labels[t]} {_node_text(name, data)}"
    if t in _IMPORTS:
        return _node_text(node, data).splitlines()[0]
    return None
