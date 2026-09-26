"""The spec corpus, read the way `validate` reads a view file.

`view-plugins/chart/spec-corpus/` is shared with the renderer's own test
(`web/src/spec.test.ts`), which runs the same files through js-yaml and the same
schema. The verdict comes from the file name (`ok-*` / `bad-*`), so the two
readers cannot quietly disagree: each is held to the same answer.

An `ok-*` file also has an `ok-*.expect.json` — the document the HOST sees,
pinned by the web test against js-yaml (the parser the SPA actually uses). This
side must produce the same document, which is what keeps a YAML 1.1 reading of
`no` or `017` from resolving a different highlight than the chart draws.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chart_view.spec import SpecError, parse_spec, spec_errors

CORPUS = Path(__file__).resolve().parents[2] / "spec-corpus"
FILES = sorted(CORPUS.glob("*.ai.yaml"))


def _verdict(text: str) -> list[str]:
    try:
        doc = parse_spec(text)
    except SpecError as e:
        return [str(e)]
    return spec_errors(doc)


def test_the_corpus_has_both_verdicts():
    names = [f.name for f in FILES]
    assert any(n.startswith("ok-") for n in names)
    assert any(n.startswith("bad-") for n in names)
    assert all(n.startswith(("ok-", "bad-")) for n in names)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_verdict_matches_the_file_name(path: Path):
    errors = _verdict(path.read_text(encoding="utf-8"))
    if path.name.startswith("ok-"):
        assert errors == []
    else:
        assert errors, "expected at least one error"


@pytest.mark.parametrize(
    "path", [f for f in FILES if f.name.startswith("ok-")], ids=lambda p: p.name
)
def test_an_ok_file_parses_to_the_document_the_host_sees(path: Path):
    expect = path.with_name(path.name.removesuffix(".ai.yaml") + ".expect.json")
    assert parse_spec(path.read_text(encoding="utf-8")) == json.loads(expect.read_text())
