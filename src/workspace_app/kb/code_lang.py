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
