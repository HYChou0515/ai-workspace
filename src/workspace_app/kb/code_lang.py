"""Single source of truth: source-file extension → tree-sitter language name.

Issue #389: code files were fed into the KB as raw text (or silently dropped)
instead of being parsed. The root cause was three independently-drifting tables
that each only knew five languages — ingest's ``_CODE_EXTENSIONS`` gate,
li_pipeline's ``CodeSplitter`` dispatch, and code_outline's skeleton extractor —
plus a reliance on libmagic's mime, which mislabels ``.go`` / ``.java`` / ``.rs``
as ``text/x-c`` (so they fell through to the SentenceSplitter or were dropped).

This module is the ONE table. The **extension is the authority** for "is this a
code file" — mime is never consulted for routing, because it's unreliable for
source code. Only genuine programming languages live here; config / markup /
data formats (yaml, sql, json, html, css) are deliberately excluded — they have
their own handling (``JSONNodeParser``, ``HtmlParser``) or read best as prose.

The grammar names are the ones the ``tree_sitter_languages`` pack exposes
(shared by LlamaIndex's ``CodeSplitter`` and our ``code_outline``); expand the
table as more languages get demand — that's now a one-line change in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

# ext (lowercased, leading dot) → tree-sitter grammar name. Each key carries the
# dot so ``endswith`` can't false-match (".h" never matches "x.hh"; ".ts" never
# matches "x.tsx"). Order is longest-suffix-safe for the same reason.
LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sc": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".lua": "lua",
    ".r": "r",
}


def code_language_for(filename: str) -> str | None:
    """The tree-sitter language name for a source file, or ``None`` when the
    extension isn't a supported programming language."""
    f = filename.lower()
    for ext, lang in LANG_BY_EXT.items():
        if f.endswith(ext):
            return lang
    return None


def is_code_file(filename: str) -> bool:
    """True iff ``filename``'s extension maps to a supported programming
    language — the authority for routing an upload to the code path (#389)."""
    return code_language_for(filename) is not None


# ── generic tree-sitter breadcrumb (#389 P2) ──
#
# A raw code chunk embeds poorly: the strongest retrieval signal is WHERE the
# code lives (module path > class > function). We recover the enclosing symbol
# chain generically — no per-language tables — by walking the AST from a chunk's
# start offset up to the root, keeping any node that *looks like* a definition.
# Node-type names are consistent enough across tree-sitter grammars that a
# substring test covers every language in ``LANG_BY_EXT``:
#   function_definition / function_declaration / function_item  → "function"
#   method_declaration / method                                 → "method"
#   class_definition / class_declaration / class                → "class"
#   impl_item → "impl"; struct_* → "struct"; module/mod_item → "mod"; …
# Unnamed matches (a Python file's root ``module``, a ``class_body``) yield no
# name and are simply skipped, so over-matching is harmless.
_DEF_MARKERS = (
    "function",
    "method",
    "class",
    "interface",
    "enum",
    "struct",
    "impl",
    "trait",
    "module",
    "mod_item",
    "namespace",
    "object",
    "constructor",
)
# Child node types that carry a definition's name when there's no ``name`` field
# (rust ``impl Foo`` → type_identifier; ruby ``class C`` → constant).
_NAME_TYPES = frozenset({"identifier", "type_identifier", "constant", "name", "field_identifier"})


def _is_definition(node_type: str) -> bool:
    return any(m in node_type for m in _DEF_MARKERS)


def _node_name(node: Node, data: bytes) -> str | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return data[named.start_byte : named.end_byte].decode("utf-8", errors="replace")
    for child in node.children:
        if child.type in _NAME_TYPES:
            return data[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return None


def symbol_path(language: str, source: str, char_offset: int) -> list[str]:
    """The enclosing symbol chain (outermost → innermost) at ``char_offset`` in
    ``source`` — e.g. ``["Validator", "validate"]`` for an offset inside a
    method body. Empty when the offset is at module level or the grammar can't
    be loaded/parsed (degrade, never raise), so the caller falls back to a
    path-only breadcrumb."""
    try:
        from tree_sitter_languages import get_parser

        parser = get_parser(language)
        data = source.encode("utf-8")
        tree = parser.parse(data)
    except Exception:  # unknown grammar / native parse error — degrade
        return []
    byte_off = len(source[:char_offset].encode("utf-8"))
    node: Node | None = tree.root_node.descendant_for_byte_range(byte_off, byte_off)
    names: list[str] = []
    while node is not None:
        if _is_definition(node.type):
            name = _node_name(node, data)
            if name:
                names.append(name)
        node = node.parent
    names.reverse()
    return names
