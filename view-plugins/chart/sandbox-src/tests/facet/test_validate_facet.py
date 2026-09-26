"""`validate` (show_file's gate) on a `facet:` spec (#848 P20): it accepted
specs facet_build then refused -- an entity source ("2 rows; v 1.5-2.5"), a
facet or sort column the data lacks ("3000000 rows; ..."), an aggregate on a
channel. It now runs the build's own checks, by running the build: one
function, so the two cannot disagree. facet_build is the oracle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chart_view.cli import main

BASE = """\
view: chart
source: data/w.csv
facet: {field: [group, item], sort: {field: rate}}
mark: grid
encoding:
  x: {field: x, type: ordinal}
  y: {field: y, type: ordinal}
  color: {field: v, type: quantitative}
"""

CSV = "group,item,x,y,v,rate,tool\n" + "".join(
    f"G1,{w},{x},{y},{w + x + y},{w / 10},{'AB'[(x + y) % 2]}\n"
    for w in (1, 2, 3)
    for x in (0, 1)
    for y in (0, 1)
)
DUPLICATE = CSV + "G1,1,0,0,9,0.1,A\n"  # a second row at (0, 0) for group (G1, 1)

VARIANTS = {
    "fine": BASE,
    "entity-source": BASE.replace("source: data/w.csv", "source: {entity: task}"),
    "no-facet-column": BASE.replace("field: [group, item]", "field: [group, nosuch]"),
    "no-sort-column": BASE.replace("sort: {field: rate}", "sort: {field: nosuch}"),
    "no-color-column": BASE.replace("color: {field: v,", "color: {field: nosuch,"),
    "aggregate-on-color": BASE.replace(
        "type: quantitative}", "type: quantitative, aggregate: mean}"
    ),
    "sort-varies": BASE.replace("sort: {field: rate}", "sort: {field: tool}"),
    "statistic-misfit": BASE.replace("sort: {field: rate}", "sort: {field: tool, stat: mean}"),
    "no-source-file": BASE.replace("data/w.csv", "data/gone.csv"),
    "duplicate-cell": BASE.replace("data/w.csv", "data/dup.csv"),
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "ws" / "data").mkdir(parents=True)
    (tmp_path / "ws" / "views").mkdir()
    (tmp_path / "ws" / "data" / "w.csv").write_text(CSV)
    (tmp_path / "ws" / "data" / "dup.csv").write_text(DUPLICATE)
    for name, text in VARIANTS.items():
        (tmp_path / "ws" / "views" / f"{name}.ai.yaml").write_text(text)
    monkeypatch.chdir(tmp_path / "ws")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _call(capsys: pytest.CaptureFixture[str], cmd: str, args: dict) -> tuple[int, str, str]:
    code = main([cmd, json.dumps(args)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize("name", list(VARIANTS))
def test_validate_refuses_exactly_what_facet_build_refuses_with_its_words(
    workspace: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    b_code, _, b_err = _call(capsys, "facet_build", {"spec": VARIANTS[name]})
    v_code, v_out, v_err = _call(capsys, "validate", {"path": f"views/{name}.ai.yaml"})
    if b_code == 0:
        assert (v_code, v_err) == (0, ""), v_err
    else:
        assert v_code == 2 and v_out == ""
        # the build's refusal, word for word (its progress lines aside)
        refusal = [ln for ln in b_err.splitlines() if not _progress(ln)]
        assert v_err.splitlines() == refusal


def _progress(line: str) -> bool:
    return line.startswith(("reading ", "read ", "left out ", "wrote ")) or " groups over " in line


def test_the_inputs_cover_both_answers(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codes = {n: _call(capsys, "facet_build", {"spec": t})[0] for n, t in VARIANTS.items()}
    assert codes["fine"] == 0
    assert sum(c == 2 for c in codes.values()) == len(VARIANTS) - 1


def test_an_accepted_gallery_is_summarised_as_a_gallery(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _call(capsys, "validate", {"path": "views/fine.ai.yaml"})
    assert code == 0
    assert out.strip() == "3 groups over 4 cells; v 1–5"


def test_a_category_gallery_says_how_many_categories(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = BASE.replace(
        "color: {field: v, type: quantitative}", "color: {field: tool, type: nominal}"
    )
    (workspace / "ws" / "views" / "cat.ai.yaml").write_text(text)
    code, out, _ = _call(capsys, "validate", {"path": "views/cat.ai.yaml"})
    assert (code, out.strip()) == (0, "3 groups over 4 cells; tool: 2 categories")


# #847/#848 PR 5 P44 row 39: a colour column with no value said "blank 0" (a
# range of one number) or "blank: 0 categories"; it is said to have none.
@pytest.mark.parametrize("kind", ["quantitative", "nominal"])
def test_a_colour_with_no_value_is_said_to_have_none(
    workspace: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    rows = CSV.splitlines()
    blank = "\n".join([rows[0] + ",blank", *(r + "," for r in rows[1:])]) + "\n"
    (workspace / "ws" / "data" / "blank.csv").write_text(blank)
    text = BASE.replace("data/w.csv", "data/blank.csv").replace(
        "color: {field: v, type: quantitative}", f"color: {{field: blank, type: {kind}}}"
    )
    (workspace / "ws" / "views" / "blank.ai.yaml").write_text(text)
    code, out, err = _call(capsys, "validate", {"path": "views/blank.ai.yaml"})
    assert (code, out.strip(), err) == (0, "3 groups over 4 cells; blank has no value", "")


def test_a_colour_of_zeros_is_still_a_range(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # (control) a column of 0s has a value: its range is one number
    rows = CSV.splitlines()
    zeros = "\n".join([rows[0] + ",zero", *(r + ",0" for r in rows[1:])]) + "\n"
    (workspace / "ws" / "data" / "zeros.csv").write_text(zeros)
    text = BASE.replace("data/w.csv", "data/zeros.csv").replace(
        "color: {field: v,", "color: {field: zero,"
    )
    (workspace / "ws" / "views" / "zeros.ai.yaml").write_text(text)
    code, out, _ = _call(capsys, "validate", {"path": "views/zeros.ai.yaml"})
    assert (code, out.strip()) == (0, "3 groups over 4 cells; zero 0")


def test_a_cache_the_build_cannot_read_back_is_a_refusal_line_too() -> None:
    """The build's other failure (exit 3: its fresh cache went before it read
    it back) is a line validate hands on, not a traceback."""
    from chart_view.facet import CacheUnusable
    from chart_view.validate import check

    def gone(_text: str) -> dict[str, Any]:
        raise CacheUnusable("c.vcache: not a facet cache in this format")

    def unread(_source: Any) -> Any:
        raise AssertionError("a gallery is checked by its build, not by query's reader")

    result = check(BASE, unread, gone)
    assert (result.summary, result.errors) == (None, ["c.vcache: not a facet cache in this format"])


def test_validating_builds_the_cache_the_gallery_then_opens(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check IS the build, so a gallery shown after it opens at once."""
    assert _call(capsys, "validate", {"path": "views/fine.ai.yaml"})[0] == 0
    code, out, _ = _call(capsys, "facet_build", {"spec": BASE})
    built: Any = json.loads(out)
    assert code == 0 and built["built"] is False
