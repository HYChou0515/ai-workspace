"""A gallery's first open shows the build's progress (plan-view-plugins-pr5-finish
P10). The runner answers a command only when it ends, so the build writes its
progress lines where ``facet_progress {spec}`` -- a second, quick command the
gallery polls while the build runs -- reads them back."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import chart_view.facet.build_command as build_command
from chart_view.cli import main
from chart_view.facet import TMP_SUFFIX, progress_file

SPEC = """\
view: chart
source: data/w.csv
facet: {field: [lot, wafer]}
mark: grid
encoding:
  x: {field: x, type: ordinal}
  y: {field: y, type: ordinal}
  color: {field: v, type: quantitative}
"""

CSV = "lot,wafer,x,y,v\n" + "".join(
    f"L1,{w},{x},{y},{w + x + y}\n" for w in (1, 2, 3) for x in (0, 1) for y in (0, 1)
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "ws" / "data").mkdir(parents=True)
    (tmp_path / "ws" / "data" / "w.csv").write_text(CSV)
    monkeypatch.chdir(tmp_path / "ws")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def _call(capsys: pytest.CaptureFixture[str], cmd: str, args: dict) -> tuple[int, Any, str]:
    code = main([cmd, json.dumps(args)])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if code == 0 else out.out), out.err


def _polled_during(
    monkeypatch: pytest.MonkeyPatch, stage: str, poll: dict | None = None
) -> list[list[str]]:
    """Poll facet_progress from inside the build, at ``stage`` (a function the
    build calls), as the gallery's timer would while the build runs."""
    seen: list[list[str]] = []
    real = getattr(build_command, stage)

    def spy(*a: Any, **k: Any) -> Any:
        # a nested command's output must not land in the build's own answer
        out = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            assert main(["facet_progress", json.dumps(poll or {"spec": SPEC})]) == 0
        finally:
            sys.stdout = out
        seen.append(json.loads(captured.getvalue())["lines"])
        return real(*a, **k)

    monkeypatch.setattr(build_command, stage, spy)
    return seen


def test_a_running_build_can_be_asked_how_far_it_is(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    at_read = _polled_during(monkeypatch, "read_source")
    code, _, err = _call(capsys, "facet_build", {"spec": SPEC})
    assert code == 0, err
    # before the source is read -- the slow part of a large first open -- the
    # gallery can already say what is happening
    assert at_read == [["reading data/w.csv"]]


def test_the_lines_polled_are_the_ones_the_build_prints(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    last: list[list[str]] = []
    real = build_command.enforce_cap

    def at_the_end(*a: Any, **k: Any) -> Any:
        last.append(progress_file(Path.home() / ".cache" / "views", SPEC).read_text().splitlines())
        return real(*a, **k)

    monkeypatch.setattr(build_command, "enforce_cap", at_the_end)
    code, _, err = _call(capsys, "facet_build", {"spec": SPEC})
    assert code == 0
    assert last == [err.strip().splitlines()]
    assert last[0][0] == "reading data/w.csv" and "3 groups over 4 cells" in last[0]


def test_a_new_build_starts_its_lines_afresh_over_a_killed_ones(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    views = Path.home() / ".cache" / "views"
    views.mkdir(parents=True)
    progress_file(views, SPEC).write_text("reading data/w.csv\nread 99 rows\n")  # a SIGKILL's
    at_read = _polled_during(monkeypatch, "read_source")
    assert _call(capsys, "facet_build", {"spec": SPEC})[0] == 0
    assert at_read == [["reading data/w.csv"]]


def test_nothing_is_left_to_poll_once_the_build_ends(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _call(capsys, "facet_build", {"spec": SPEC})[0] == 0
    assert not progress_file(Path.home() / ".cache" / "views", SPEC).exists()
    code, answer, _ = _call(capsys, "facet_progress", {"spec": SPEC})
    assert (code, answer) == (0, {"lines": []})


def test_a_build_that_fails_leaves_nothing_to_poll_either(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = SPEC.replace("color: {field: v", "color: {field: nope")
    assert _call(capsys, "facet_build", {"spec": bad})[0] == 2
    assert not progress_file(Path.home() / ".cache" / "views", bad).exists()


def test_a_progress_file_a_kill_leaves_behind_is_the_caps_to_sweep(tmp_path: Path) -> None:
    """A SIGKILL skips the cleanup; the file is named as a temp file, which the
    cache cap removes once it is older than any build takes."""
    assert progress_file(tmp_path, SPEC).name.endswith(TMP_SUFFIX)


def test_progress_is_per_spec_text(tmp_path: Path) -> None:
    assert progress_file(tmp_path, SPEC) != progress_file(tmp_path, SPEC + "# edited\n")
    assert progress_file(tmp_path, SPEC).parent == tmp_path


@pytest.mark.parametrize("args", [{}, {"spec": 3}, {"spec": SPEC, "extra": 1}])
def test_a_wrong_progress_call_exits_2(
    workspace: Path, capsys: pytest.CaptureFixture[str], args: dict
) -> None:
    code, _, err = _call(capsys, "facet_progress", args)
    assert code == 2 and err.strip()


def test_polling_never_loads_pandas(workspace: Path) -> None:
    """It runs every second while a build does: the build pays for pandas, the
    poll must not."""
    probe = (
        "import sys, json\n"
        "from chart_view.cli import main\n"
        f"main(['facet_progress', json.dumps({{'spec': {SPEC!r}}})])\n"
        "print('PANDAS' if 'pandas' in sys.modules else 'CLEAN', file=sys.stderr)\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "HOME": str(workspace / "home")},
        capture_output=True,
        text=True,
    )
    assert run.stderr.strip().splitlines()[-1] == "CLEAN", run.stderr


# ─── the view as its file (#847/#848 P9), and a sort beside it (P4) ────────


def test_a_build_of_a_view_file_is_polled_with_the_same_arguments(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gallery hands facet_build the view's FILE, and polls with the same
    arguments: the progress is named by them, so the poll reads no file."""
    (workspace / "ws" / "views").mkdir()
    (workspace / "ws" / "views" / "g.ai.yaml").write_text(SPEC)
    call = {"path": "views/g.ai.yaml", "rev": "r1"}
    mine = _polled_during(monkeypatch, "read_source", call)
    other = _polled_during(monkeypatch, "apply_transforms", {**call, "sort": None})
    code, _, err = _call(capsys, "facet_build", {**call, "epoch": 0})
    assert code == 0, err
    assert mine == [["reading data/w.csv"]]
    assert other == [[]]  # another sort is another build


def test_a_calls_name_is_its_arguments_epoch_aside() -> None:
    from chart_view.facet import view_ident

    call = {"path": "v.ai.yaml", "rev": "r"}
    assert view_ident({**call, "epoch": 3}) == view_ident(call)
    assert view_ident(call) != view_ident({**call, "rev": "r2"})
    assert view_ident(call) != view_ident({**call, "sort": None})
    assert view_ident({"spec": SPEC, "epoch": 1}) == SPEC
