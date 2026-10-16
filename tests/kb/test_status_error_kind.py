"""docs/plan-friendly-errors.md P5: `SourceDoc.status_error_kind` names the
failure `status_detail` describes. The two are written together everywhere, or a
kind outlives the text it described — a doc that later stops with "processing
was interrupted" would then be worded as the rate limit it hit last week.

There are seven writers today, in two modules, and the next one will be added by
someone who has never read this. So the rule is checked over the source: every
call that passes `status_detail=` passes `status_error_kind=` beside it — the
writers, and the API responses that carry them to the page."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "workspace_app"


def _calls_missing_the_kind() -> list[str]:
    missing: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            names = {kw.arg for kw in node.keywords}
            if "status_detail" in names and "status_error_kind" not in names:
                missing.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return missing


def test_every_status_detail_write_names_its_kind_too() -> None:
    assert _calls_missing_the_kind() == []


def test_the_check_sees_a_writer_that_forgets() -> None:
    """The guard's own control: the shape it looks for is the shape the
    writers have, so a forgotten kind is found rather than walked past."""
    tree = ast.parse("replace(doc, status='error', status_detail='x')")
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    names = {kw.arg for kw in call.keywords}
    assert "status_detail" in names and "status_error_kind" not in names
