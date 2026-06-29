"""Issue #281: ``code_outline.outline`` extracts a deterministic symbol
skeleton (top-level imports / functions / classes) from a source file via
tree-sitter — the faithful backbone of an L0 file card, so the LLM one-liner
rides on top of an outline that never hallucinates or misses a definition.
Unsupported languages / parse failures degrade to an empty string."""

from __future__ import annotations

from workspace_app.kb.wiki.code_outline import outline


def test_python_outline_lists_top_level_defs_and_classes():
    src = (
        "import os\n"
        "\n"
        "def bar(x):\n"
        "    return x\n"
        "\n"
        "class Baz:\n"
        "    def method(self):\n"
        "        pass\n"
    )
    out = outline("foo.py", src)
    assert "def bar" in out
    assert "class Baz" in out
    # nested defs are NOT top-level — the outline is a skeleton, not a dump
    assert "method" not in out


def test_python_outline_sees_through_decorators():
    # A decorated top-level def/class is wrapped in a `decorated_definition`
    # node — real code is full of these, so the skeleton must unwrap them.
    src = "@app.route('/x')\ndef handler():\n    pass\n\n@dataclass\nclass Config:\n    pass\n"
    out = outline("foo.py", src)
    assert "def handler" in out
    assert "class Config" in out


def test_typescript_outline_handles_exports_and_plain_decls():
    src = (
        "import { useState } from 'react'\n"
        "\n"
        "export function useThing() {}\n"
        "export class Widget {}\n"
        "function helper() {}\n"
        "class Internal {}\n"
    )
    out = outline("foo.ts", src)
    assert "function useThing" in out  # seen through `export`
    assert "class Widget" in out
    assert "function helper" in out  # plain top-level decl
    assert "class Internal" in out


def test_tsx_outline_parses():
    # .tsx uses a distinct grammar — make sure it routes to a real parser.
    out = outline("App.tsx", "export function App() { return null }\n")
    assert "function App" in out


def test_skips_top_level_statements_and_bare_reexports():
    # A top-level constant (not a def/class/import) yields no outline line; a
    # bare `export { … } from` re-export has no `declaration` to unwrap.
    py = outline("foo.py", "VERSION = 3\n\ndef go():\n    pass\n")
    assert "def go" in py
    assert "VERSION" not in py
    ts = outline("foo.ts", "export { thing } from './other'\nexport function keep() {}\n")
    assert "function keep" in ts
    assert "thing" not in ts  # the re-export contributes nothing


def test_unsupported_extension_yields_empty():
    assert outline("notes.md", "# heading\nsome prose") == ""
    assert outline("Main.go", "package main\nfunc main() {}") == ""


def test_parser_failure_degrades_to_empty(monkeypatch):
    # A native grammar / parse explosion must not wedge a whole-repo build —
    # the file simply gets no skeleton (its LLM one-liner still works).
    def boom(_lang):
        raise RuntimeError("grammar blew up")

    monkeypatch.setattr("tree_sitter_languages.get_parser", boom)
    assert outline("foo.py", "def x(): pass") == ""
