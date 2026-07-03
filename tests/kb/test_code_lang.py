"""Single source of truth for source-file → tree-sitter language (#389).

The extension is the routing authority — libmagic mislabels most source
files, so these are pure filename → language mappings.
"""

from __future__ import annotations

import pytest

from workspace_app.kb.code_lang import LANG_BY_EXT, code_language_for, is_code_file


@pytest.mark.parametrize(
    ("filename", "lang"),
    [
        ("m.py", "python"),
        ("app.ts", "typescript"),
        ("App.tsx", "tsx"),
        ("util.js", "javascript"),
        # The languages issue #389 was silently dropping / shredding:
        ("svc.go", "go"),
        ("Main.java", "java"),
        ("lib.rs", "rust"),
        ("main.c", "c"),
        ("engine.cpp", "cpp"),
        ("Program.cs", "c_sharp"),
        ("model.rb", "ruby"),
        ("index.php", "php"),
        ("View.kt", "kotlin"),
        ("Job.scala", "scala"),
        ("deploy.sh", "bash"),
        ("script.lua", "lua"),
        ("analysis.R", "r"),  # case-insensitive
    ],
)
def test_code_language_for_maps_supported_languages(filename: str, lang: str):
    assert code_language_for(filename) == lang
    assert is_code_file(filename) is True


@pytest.mark.parametrize(
    "filename",
    ["notes.md", "data.json", "config.yaml", "query.sql", "page.html", "sheet.csv", "readme.txt"],
)
def test_config_markup_and_prose_are_not_code(filename: str):
    """Config / markup / data formats are handled elsewhere (JSONNodeParser,
    HtmlParser) or read best as prose — they must NOT route to the code path."""
    assert code_language_for(filename) is None
    assert is_code_file(filename) is False


def test_dotted_extensions_do_not_false_match():
    """`.h` (C header) must not swallow `.hh`/`.hpp` (C++), and `.ts` must not
    swallow `.tsx` — every key carries its leading dot so `endswith` is exact."""
    assert code_language_for("a.hh") == "cpp"
    assert code_language_for("a.hpp") == "cpp"
    assert code_language_for("a.h") == "c"
    assert code_language_for("a.tsx") == "tsx"
    assert code_language_for("a.ts") == "typescript"


def test_every_language_grammar_actually_loads():
    """Guard: every grammar name in the table must be loadable from the
    `tree_sitter_languages` pack — a typo would silently disable a language."""
    from tree_sitter_languages import get_parser

    for lang in set(LANG_BY_EXT.values()):
        get_parser(lang)  # raises on an unknown grammar name
