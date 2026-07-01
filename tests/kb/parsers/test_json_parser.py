"""JsonParser — issue #39 P7.

Locked decisions (docs/plan-kb-parsers.md §2-P7):
  - matches: `.json` + `.jsonl` (extension) or `application/json` mime
  - `.json` → ONE whole-file Document (chunking-hyperparams principle:
    granularity belongs to the splitter, not the parser)
  - `.jsonl` → one Document per line (each line is an independent record)
  - the actual JSON-aware splitting happens downstream in
    `DispatchSplitter`'s JSON branch (JSONNodeParser) — see
    tests/kb/test_li_pipeline.py
"""

from __future__ import annotations

import pytest

from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.json_file import JsonParser


def _input(data: bytes, filename: str = "x.json") -> MaterialisedParserInput:
    return MaterialisedParserInput(data, filename=filename)


# ── matches ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "mime", "expected"),
    [
        ("data.json", "application/json", True),
        ("data.json", "text/plain", True),  # libmagic often sniffs json as text
        ("records.jsonl", "text/plain", True),
        ("noext", "application/json", True),  # mime alone is enough
        ("notes.txt", "text/plain", False),
        ("data.csv", "text/csv", False),
    ],
)
def test_matches_by_extension_or_mime(filename: str, mime: str, expected: bool):
    p = JsonParser()
    got = p.matches(filename=filename, mime=mime, source=_input(b"{}", filename))
    assert got is expected


def test_json_parser_inherits_base_no_guidance_328():
    # #328: a non-prompt-driven parser (JSON has no model prompt to append to)
    # inherits the base `uses_guidance()` default of False, so the ingestor
    # never threads the collection's parser_guidance through it.
    assert JsonParser().uses_guidance() is False


# ── parse: .json ─────────────────────────────────────────────────────


def test_json_file_becomes_one_document_with_raw_text():
    """Whole file → ONE Document carrying the decoded JSON text and
    filename/mime metadata (so DispatchSplitter can route it to the
    JSON branch)."""
    data = b'{"users": [{"name": "Bob", "email": "bob@x.com"}]}'
    p = JsonParser()
    docs = list(p.parse(_input(data), filename="users.json", mime="application/json"))
    assert len(docs) == 1
    assert docs[0].text == data.decode("utf-8")
    assert docs[0].metadata["filename"] == "users.json"
    assert docs[0].metadata["mime"] == "application/json"


def test_invalid_json_raises_for_status_error():
    """Malformed JSON must raise (→ Ingestor flips status=error with the
    message in status_detail, Q10) rather than silently producing zero
    chunks."""
    p = JsonParser()
    with pytest.raises(ValueError, match="invalid JSON"):
        p.parse(_input(b"{nope"), filename="bad.json", mime="application/json")


# ── parse: .jsonl ────────────────────────────────────────────────────


def test_jsonl_becomes_one_document_per_line():
    """JSON Lines: each line is an independent record → one Document per
    line, so the JSON splitter flattens each record on its own and a
    record never straddles a chunk boundary."""
    data = b'{"name": "Bob"}\n{"name": "Amy"}\n\n{"name": "Joe"}\n'
    p = JsonParser()
    docs = list(p.parse(_input(data, "r.jsonl"), filename="r.jsonl", mime="text/plain"))
    assert [d.text for d in docs] == ['{"name": "Bob"}', '{"name": "Amy"}', '{"name": "Joe"}']
    # Record position survives into metadata for citation labelling.
    assert [d.metadata["jsonl_line"] for d in docs] == [1, 2, 4]


def test_jsonl_with_invalid_line_raises():
    p = JsonParser()
    with pytest.raises(ValueError, match="line 2"):
        p.parse(
            _input(b'{"ok": 1}\n{broken\n', "r.jsonl"),
            filename="r.jsonl",
            mime="text/plain",
        )


# ── fan-out unit seam (#227) ─────────────────────────────────────────


def test_json_array_count_units_is_element_count():
    """A top-level JSON array is a record list → one element = one unit,
    so a giant array fans out instead of embedding in a single job."""
    p = JsonParser()
    data = b'[{"a": 1}, {"a": 2}, {"a": 3}]'
    assert p.count_units(_input(data), filename="x.json", mime="application/json") == 3


def test_json_object_root_is_a_single_unit():
    """A non-array root stays one indivisible unit (whole-file Document,
    splitter owns granularity) — never fanned out."""
    import json

    p = JsonParser()
    data = b'{"a": 1, "b": 2}'
    assert p.count_units(_input(data), filename="x.json", mime="application/json") == 1
    # Malformed too → 1 unit, so the error surfaces via parse() as before.
    assert p.count_units(_input(b"{nope"), filename="x.json", mime="application/json") == 1
    docs = list(p.parse(_input(data), filename="x.json", mime="application/json"))
    assert len(docs) == 1 and json.loads(docs[0].text) == {"a": 1, "b": 2}


def test_json_array_parse_unit_range_slices_elements():
    """A process job's unit_range yields a Document holding ONLY its array
    slice (still valid JSON → the splitter's JSON branch flattens it)."""
    import json

    p = JsonParser()
    data = b'[{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}]'
    docs = list(
        p.parse(_input(data), filename="x.json", mime="application/json", unit_range=(1, 3))
    )
    assert len(docs) == 1
    assert json.loads(docs[0].text) == [{"a": 2}, {"a": 3}]


def test_jsonl_count_units_and_unit_range():
    """JSONL: non-empty lines are the units (blank lines don't count);
    unit_range slices the record list while metadata keeps real line nos."""
    import json

    p = JsonParser()
    data = b'{"a": 1}\n{"a": 2}\n\n{"a": 3}\n'
    assert p.count_units(_input(data, "r.jsonl"), filename="r.jsonl", mime="") == 3
    docs = list(p.parse(_input(data, "r.jsonl"), filename="r.jsonl", mime="", unit_range=(1, 3)))
    assert [json.loads(d.text) for d in docs] == [{"a": 2}, {"a": 3}]
    assert [d.metadata["jsonl_line"] for d in docs] == [2, 4]
