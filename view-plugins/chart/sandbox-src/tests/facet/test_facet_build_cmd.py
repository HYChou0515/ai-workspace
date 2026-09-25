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


def test_a_nominal_number_colour_is_categories_as_the_full_grid_draws_it(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """query sends a grid colour that is not quantitative as `cat`, whatever
    the column's dtype; a thumbnail painted as a ramp would disagree."""
    spec = SPEC.replace("color: {field: v, type: quantitative}", "color: {field: v, type: nominal}")
    _, built, err = _call(capsys, "facet_build", {"spec": spec})
    assert isinstance(built, dict), err
    _, index, _ = _call(capsys, "facet_index", {"key": built["key"]})
    assert isinstance(index, dict)
    assert index["scale"]["kind"] == "category"
    assert index["scale"]["labels"] == sorted(index["scale"]["labels"])
    assert "10" in index["scale"]["labels"]  # the marking string of 10


def test_an_encoding_aggregate_is_refused_pointing_at_transform(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = SPEC.replace(
        "color: {field: v, type: quantitative}",
        "color: {field: v, type: quantitative, aggregate: mean}",
    )
    code, _, err = _call(capsys, "facet_build", {"spec": spec})
    assert code == 2 and "transform" in err


def test_what_does_not_shape_the_cache_does_not_rebuild_it(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sorting the other way, or retitling, reuses the index in hand (P6)."""
    _, first, _ = _call(capsys, "facet_build", {"spec": SPEC})
    reordered = SPEC.replace("order: descending", "order: ascending").replace(
        "x: {field: x, type: ordinal}", "x: {field: x, type: ordinal, title: Column}"
    )
    _, second, _ = _call(capsys, "facet_build", {"spec": reordered})
    assert isinstance(first, dict) and isinstance(second, dict)
    assert second["key"] == first["key"] and second["built"] is False


@pytest.mark.parametrize("spelling", ["/data/w.csv", "./data/w.csv", "data//w.csv"])
def test_one_file_spelled_another_way_is_the_same_cache(
    workspace: Path, capsys: pytest.CaptureFixture[str], spelling: str
) -> None:
    _, first, _ = _call(capsys, "facet_build", {"spec": SPEC})
    _, second, _ = _call(capsys, "facet_build", {"spec": SPEC.replace("data/w.csv", spelling)})
    assert isinstance(first, dict) and isinstance(second, dict)
    assert second["key"] == first["key"] and second["built"] is False


def test_the_file_keyed_is_the_file_read(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`link/../data/w.csv` folds to data/w.csv as text, but the OS walks the
    symlink first and lands in another directory. Keying one path and reading
    the other served one file's thumbnails under another file's key."""
    ws = workspace / "ws"
    (ws / "sub" / "data").mkdir(parents=True)
    (ws / "sub" / "data" / "w.csv").write_text(CSV.replace("L1,", "L9,"))
    (ws / "sub" / "deep").mkdir()
    (ws / "link").symlink_to(ws / "sub" / "deep")
    _, via_link, err = _call(
        capsys, "facet_build", {"spec": SPEC.replace("data/w.csv", "link/../data/w.csv")}
    )
    assert isinstance(via_link, dict), err
    _, index, _ = _call(capsys, "facet_index", {"key": via_link["key"]})
    assert isinstance(index, dict)
    # the cache is keyed on data/w.csv (lot L1), so it must hold data/w.csv's rows
    assert {g["key"][0] for g in index["groups"]} == {"L1"}


def test_reusing_a_cache_marks_it_recently_used(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, built, _ = _call(capsys, "facet_build", {"spec": SPEC})
    assert isinstance(built, dict)
    path = workspace / "home" / ".cache" / "views" / f"{built['key']}.vcache"
    os.utime(path, (1, 1))
    _call(capsys, "facet_build", {"spec": SPEC})
    assert path.stat().st_mtime > 1


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


# #847/#848 P9: the gallery names its view FILE, as `validate` does. Its text in
# argv (one string, capped at 128 KiB by the kernel) let a big spec pass
# show_file and fail every render with a 413.
def _big_view(workspace: Path) -> str:
    keep = ", ".join(f"L{i}" for i in range(30_000))
    text = SPEC + f"transform:\n  - filter: {{field: lot, oneOf: [{keep}]}}\n"
    assert len(text.encode()) > 128 * 1024
    (workspace / "ws" / "views").mkdir()
    (workspace / "ws" / "views" / "g.ai.yaml").write_text(text)
    return "/views/g.ai.yaml"


def test_the_build_reads_the_view_file_it_is_named(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _big_view(workspace)
    args = {"path": path, "rev": "0" * 11, "epoch": 0}
    assert len(json.dumps(args)) < 200  # the call's size is not the spec's
    code, by_path, err = _call(capsys, "facet_build", args)
    assert code == 0, err
    # the oracle is the text form: the same file, named the other way, is the
    # same cache (reused, not rebuilt)
    text = (workspace / "ws" / path[1:]).read_text()
    code, by_text, _ = _call(capsys, "facet_build", {"spec": text})
    assert code == 0 and isinstance(by_path, dict) and isinstance(by_text, dict)
    assert by_path["built"] is True and by_text["built"] is False
    assert (by_text["key"], by_text["build"]) == (by_path["key"], by_path["build"])
    assert by_path["groups"] == 3


def test_the_build_names_a_view_file_that_is_not_there(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _call(capsys, "facet_build", {"path": "views/nope.ai.yaml", "rev": "r"})
    # that reason alone: nothing is built from a file that was not read
    assert (code, out, err) == (
        2,
        "",
        "views/nope.ai.yaml is not a readable text file in the workspace\n",
    )


@pytest.mark.parametrize(
    "args",
    [
        {"spec": SPEC, "path": "views/g.ai.yaml"},
        {"rev": "r"},
        {"path": 3},
        {"path": "views/g.ai.yaml", "rev": 3},
        {"spec": SPEC, "rev": "r"},
    ],
    ids=["both", "neither", "path-not-text", "rev-not-text", "rev-without-path"],
)
def test_the_build_takes_a_spec_or_a_path(
    workspace: Path, capsys: pytest.CaptureFixture[str], args: dict
) -> None:
    (workspace / "ws" / "views").mkdir()
    (workspace / "ws" / "views" / "g.ai.yaml").write_text(SPEC)
    code, out, err = _call(capsys, "facet_build", args)
    assert (code, out) == (2, "") and err.strip()
