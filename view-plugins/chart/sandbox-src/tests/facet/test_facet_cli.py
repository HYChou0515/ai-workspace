"""The pager's launch commands (plan-view-plugins-pr4 P4), under the bundle's
contract: `launch <cmd> '<json args>'`, answer on stdout.

Exit codes the gallery acts on:
- 0: the answer (JSON) on stdout;
- 2: the call itself is wrong (arguments), lines on stderr;
- 3: the cache cannot be used (missing after a reap, cut short, corrupt) --
  build it again;
- 4: the cache was rebuilt since the index the call names -- refetch the index.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chart_view.cli import main
from chart_view.facet import CacheKey, ContinuousScale, Group, cache_file, write_cache

KEY = CacheKey(source_path="data/w.csv", size=1, mtime_ns=1, transform_hash="t")


@pytest.fixture
def views(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The isolated launcher sets HOME to the sandbox's .home; the cache lives
    in .home/.cache/views (Q12)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".cache" / "views"
    root.mkdir(parents=True)
    return root


def _build(root: Path) -> str:
    write_cache(
        cache_file(root, KEY),
        scale=ContinuousScale(0.0, 254.0),
        facet=["g"],
        cells=1,
        layout={"x": [0], "y": [0]},
        groups=[Group(key=(f"g{i}",), sort={}, values=[float(i)]) for i in range(3)],
    )
    return KEY.digest()


def _run(capsys: pytest.CaptureFixture[str], cmd: str, args: dict) -> tuple[int, str, str]:
    code = main([cmd, json.dumps(args)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_command_list_names_the_pager_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    names = [c["name"] for c in json.loads(capsys.readouterr().out)]
    assert {"facet_index", "facet_page", "facet_exact"} <= set(names)


@pytest.mark.parametrize("cmd", ["facet_index", "facet_page", "facet_exact"])
def test_a_pager_command_alone_describes_its_arguments(
    capsys: pytest.CaptureFixture[str], cmd: str
) -> None:
    assert main([cmd]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["name"] == cmd and meta["params_json_schema"]["type"] == "object"


def test_index_page_and_exact_answer_from_the_cache_in_home(
    views: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = _build(views)
    code, out, _ = _run(capsys, "facet_index", {"key": key})
    assert code == 0
    index = json.loads(out)
    assert [g["key"] for g in index["groups"]] == [["g0"], ["g1"], ["g2"]]

    code, out, _ = _run(
        capsys, "facet_page", {"key": key, "build": index["build"], "positions": [2, 0]}
    )
    assert code == 0 and [g["kind"] for g in json.loads(out)["groups"]] == ["q8", "q8"]

    code, out, _ = _run(capsys, "facet_exact", {"key": key, "build": index["build"], "position": 1})
    assert code == 0 and json.loads(out)["kind"] == "f64"


def test_a_missing_cache_exits_3_for_the_gallery_to_build_it(
    views: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(capsys, "facet_index", {"key": KEY.digest()})
    assert (code, out) == (3, "") and err


@pytest.mark.parametrize("cmd", ["facet_page", "facet_exact"])
def test_a_read_against_an_older_build_exits_4_for_the_gallery_to_refetch(
    views: Path, capsys: pytest.CaptureFixture[str], cmd: str
) -> None:
    key = _build(views)
    old = json.loads(_run(capsys, "facet_index", {"key": key})[1])["build"]
    _build(views)
    args = (
        {"key": key, "build": old, "positions": [0]}
        if cmd == "facet_page"
        else {
            "key": key,
            "build": old,
            "position": 0,
        }
    )
    code, out, err = _run(capsys, cmd, args)
    assert (code, out) == (4, "") and err


@pytest.mark.parametrize(
    ("cmd", "args"),
    [
        ("facet_index", {}),
        ("facet_index", {"key": "../../etc/passwd"}),
        ("facet_index", {"key": "a" * 64, "extra": 1}),
        ("facet_page", {"key": "a" * 64, "build": "b" * 32, "positions": "0"}),
        ("facet_page", {"key": "a" * 64, "build": "b" * 32, "positions": [True]}),
        ("facet_page", {"key": "a" * 64, "build": "b" * 32, "positions": [99]}),
        ("facet_exact", {"key": "a" * 64, "build": "b" * 32, "position": 1.5}),
        ("facet_page", {"key": "a" * 64, "build": 7, "positions": [0]}),
        ("facet_page", {"key": "a" * 64, "build": "b" * 32, "positions": ""}),
        ("facet_page", {"key": "a" * 64, "build": "b" * 32, "positions": {}}),
    ],
    ids=[
        "no-key",
        "key-is-a-path",
        "unknown-argument",
        "positions-not-a-list",
        "position-a-bool",
        "position-out-of-range",
        "position-a-float",
        "build-not-text",
        "positions-empty-text",
        "positions-empty-object",
    ],
)
def test_a_wrong_call_exits_2_naming_what_is_wrong(
    views: Path, capsys: pytest.CaptureFixture[str], cmd: str, args: dict
) -> None:
    if args.get("key") == "a" * 64:  # a real cache, so only the argument is wrong
        args = {**args, "key": _build(views)}
        index = json.loads(_run(capsys, "facet_index", {"key": args["key"]})[1])
        if isinstance(args.get("build"), str):
            args["build"] = index["build"]
    code, out, err = _run(capsys, cmd, args)
    assert (code, out) == (2, "") and err.strip()


@pytest.mark.parametrize(
    "args",
    [
        {"key": KEY.digest(), "build": "b" * 32, "positions": None},
        {"key": KEY.digest(), "build": "b" * 32, "positions": [0.5]},
    ],
    ids=["positions-null", "positions-float"],
)
def test_a_wrong_call_is_named_before_the_cache_is_looked_for(
    views: Path, capsys: pytest.CaptureFixture[str], args: dict
) -> None:
    """With no cache yet, a wrong call must still be 2, not 3: 3 sends the
    gallery to build a cache for a call that will then fail anyway."""
    code, out, err = _run(capsys, "facet_page", args)
    assert (code, out) == (2, "") and err.strip()


def test_a_wrong_exact_position_is_named_before_the_cache_is_looked_for(
    views: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = {"key": KEY.digest(), "build": "b" * 32, "position": "0"}
    code, out, err = _run(capsys, "facet_exact", args)
    assert (code, out) == (2, "") and err.strip()


@pytest.mark.parametrize("cmd", ["facet_index", "query"])
def test_arguments_nested_past_the_decoder_exit_2_not_a_traceback(
    capsys: pytest.CaptureFixture[str], cmd: str
) -> None:
    """Within the runner's 128 KiB argv, JSON can nest deeper than Python's
    decoder recurses."""
    raw = "[" * 60_000 + "]" * 60_000
    assert len(raw) < 131_072
    assert main([cmd, raw]) == 2
    assert "JSON" in capsys.readouterr().err


def test_arguments_that_are_not_json_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["facet_index", "{nope"]) == 2
    assert "JSON" in capsys.readouterr().err


def test_the_pager_commands_never_load_pandas(views: Path) -> None:
    """They run on every scroll; the builder and query pay the pandas import,
    these must not. Checked in a fresh process: this one loaded it long ago."""
    key = _build(views)
    probe = (
        "import sys, json\n"
        "from chart_view.cli import main\n"
        f"main(['facet_index', json.dumps({{'key': {key!r}}})])\n"
        "print('PANDAS' if 'pandas' in sys.modules else 'CLEAN', file=sys.stderr)\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "HOME": str(views.parent.parent)},
        capture_output=True,
        text=True,
    )
    assert run.stderr.strip().splitlines()[-1] == "CLEAN", run.stderr
