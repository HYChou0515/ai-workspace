"""`facet_build {"spec"}` (plan-view-plugins-pr4 P3/P6): a `facet:` spec in,
the cache it opens as a gallery out -- built once per (source version, spec),
bounded by the cap on every build."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chart_view.cli import main

SPEC = """\
view: chart
source: data/w.csv
facet: {field: [lot, wafer], sort: {field: rate, order: descending}}
mark: grid
encoding:
  x: {field: x, type: ordinal}
  y: {field: y, type: ordinal}
  color: {field: v, type: quantitative}
"""

CSV = "lot,wafer,x,y,v,rate\n" + "".join(
    f"L1,{w},{x},{y},{w * 10 + x + y},{w / 10}\n" for w in (1, 2, 3) for x in (0, 1) for y in (0, 1)
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "ws" / "data").mkdir(parents=True)
    (tmp_path / "ws" / "data" / "w.csv").write_text(CSV)
    monkeypatch.chdir(tmp_path / "ws")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _call(capsys: pytest.CaptureFixture[str], cmd: str, args: dict) -> tuple[int, dict | str, str]:
    code = main([cmd, json.dumps(args)])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if code == 0 else out.out), out.err


def test_the_build_answers_a_key_the_pager_opens(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, built, err = _call(capsys, "facet_build", {"spec": SPEC})
    assert code == 0, err
    assert isinstance(built, dict)
    assert built["built"] is True and built["groups"] == 3 and built["cells"] == 4
    code, index, _ = _call(capsys, "facet_index", {"key": built["key"]})
    assert code == 0 and isinstance(index, dict)
    assert index["build"] == built["build"]
    assert [g["key"] for g in index["groups"]] == [["L1", "1"], ["L1", "2"], ["L1", "3"]]
    assert [g["sort"] for g in index["groups"]] == [{"rate": 0.1}, {"rate": 0.2}, {"rate": 0.3}]
    assert "read 12 rows" in err  # the progress lines


def test_the_same_spec_on_the_same_source_reuses_the_cache(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, first, _ = _call(capsys, "facet_build", {"spec": SPEC})
    _, second, _ = _call(capsys, "facet_build", {"spec": SPEC})
    assert isinstance(first, dict) and isinstance(second, dict)
    assert second["built"] is False
    assert (second["key"], second["build"]) == (first["key"], first["build"])


def test_an_edited_source_is_a_new_cache(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, first, _ = _call(capsys, "facet_build", {"spec": SPEC})
    source = workspace / "ws" / "data" / "w.csv"
    source.write_text(CSV + "L1,4,0,0,1,0.4\n")
    _, second, _ = _call(capsys, "facet_build", {"spec": SPEC})
    assert isinstance(first, dict) and isinstance(second, dict)
    assert second["key"] != first["key"] and second["built"] is True
    assert second["groups"] == 4


def test_a_same_size_edit_is_a_new_cache_by_its_mtime(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, first, _ = _call(capsys, "facet_build", {"spec": SPEC})
    source = workspace / "ws" / "data" / "w.csv"
    before = source.stat()
    edited = CSV.replace("L1,1,0,0,10,0.1", "L1,1,0,0,11,0.1")
    assert len(edited) == len(CSV)
    source.write_text(edited)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 10**9))
    _, second, _ = _call(capsys, "facet_build", {"spec": SPEC})
    assert isinstance(first, dict) and isinstance(second, dict)
    assert second["key"] != first["key"] and second["built"] is True


def test_a_changed_spec_is_a_new_cache_but_a_changed_cap_is_not(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, first, _ = _call(capsys, "facet_build", {"spec": SPEC})
    capped = SPEC.replace("order: descending}}", "order: descending}, cache_mb: 50}")
    _, same, _ = _call(capsys, "facet_build", {"spec": capped})
    other = SPEC.replace("type: quantitative", "type: nominal")
    _, new, _ = _call(capsys, "facet_build", {"spec": other})
    assert isinstance(first, dict) and isinstance(same, dict) and isinstance(new, dict)
    assert same["key"] == first["key"]
    assert new["key"] != first["key"]


def test_every_build_bounds_the_cache_dir_by_the_spec_cap(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    views = workspace / "home" / ".cache" / "views"
    views.mkdir(parents=True)
    old = views / ("0" * 64 + ".vcache")
    old.write_bytes(b"x" * (2 * 1024 * 1024))
    os.utime(old, (1, 1))  # least recently used
    capped = SPEC.replace("order: descending}}", "order: descending}, cache_mb: 1}")
    code, _, err = _call(capsys, "facet_build", {"spec": capped})
    assert code == 0, err
    assert not old.exists()


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        (
            SPEC.replace(
                "facet: {field: [lot, wafer], sort: {field: rate, order: descending}}\n", ""
            ),
            "facet",
        ),
        (SPEC.replace("mark: grid", "mark: scatter"), "grid"),
        (SPEC.replace("source: data/w.csv", "source: {entity: task}"), "table file"),
        (SPEC.replace("data/w.csv", "data/gone.csv"), "gone.csv"),
        (SPEC.replace("field: v,", "field: nope,"), "'nope'"),
        ("view: [chart", ""),
    ],
    ids=["no-facet", "not-a-grid", "entity-source", "no-such-file", "no-such-column", "bad-yaml"],
)
def test_a_spec_the_gallery_cannot_build_exits_2_naming_why(
    workspace: Path, capsys: pytest.CaptureFixture[str], spec: str, fragment: str
) -> None:
    code, out, err = _call(capsys, "facet_build", {"spec": spec})
    assert (code, out) == (2, "")
    assert fragment in err and err.strip()


@pytest.mark.parametrize(
    "colour",
    ["", "  color: {value: red}\n"],
    ids=["no-color-channel", "a-constant-color"],
)
def test_a_facet_build_without_a_color_field_exits_2(
    workspace: Path, capsys: pytest.CaptureFixture[str], colour: str
) -> None:
    spec = SPEC.replace("  color: {field: v, type: quantitative}\n", colour)
    code, _, err = _call(capsys, "facet_build", {"spec": spec})
    assert code == 2 and "color" in err


def test_a_spec_that_is_not_text_exits_2(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _call(capsys, "facet_build", {"spec": 5})
    assert code == 2 and "spec" in err
