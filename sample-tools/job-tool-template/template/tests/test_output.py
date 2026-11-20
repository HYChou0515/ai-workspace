"""What leaves the process: stdout holds exactly the answer, the exit code is
the one the outcome calls for, and the process ends when the answer is given."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import pytest
from backends import Chatty, Exiting, Machine, Remote, RemoteWithQueue

from job_tool.cli import main

TESTS = Path(__file__).resolve().parent
LOGS = ["logs", json.dumps({"job_id": "j1", "offset": 0})]


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Remote.machine = Machine()


def _strict(text: str):
    """json.loads that refuses NaN/Infinity, which are not JSON."""

    def refuse(token):
        raise ValueError(f"not JSON: {token}")

    return json.loads(text, parse_constant=refuse)


def _entry(backend: str, argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """The installed command, in a real process with real file descriptors."""
    program = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        f"from backends import {backend}; from job_tool.cli import entry;"
        f"raise SystemExit(entry(json.loads(sys.argv[2]), backend={backend}))"
    )
    return subprocess.run(
        [sys.executable, "-c", program, str(TESTS), json.dumps(argv)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )


def test_the_process_ends_at_the_budget_even_when_the_backend_waits_on_a_pool(tmp_path):
    # The interpreter joins pool workers at exit; abandoning the call is not
    # enough unless the process also refuses to wait for them.
    started = time.monotonic()
    proc = _entry("Pooled", LOGS, tmp_path)
    took = time.monotonic() - started

    assert proc.returncode == 2, proc.stderr
    assert "0.2s" in proc.stderr
    assert took < 10  # the pool sleeps 30


def test_what_a_child_process_of_the_backend_writes_never_reaches_the_answer(tmp_path):
    proc = _entry("Shelling", LOGS, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert _strict(proc.stdout)["text"] == "ok"  # exactly one JSON document
    assert "CHILD-OUTPUT" in proc.stderr


def test_what_the_backend_left_running_never_follows_the_answer(tmp_path):
    # The installed command exits right after `main`; whatever speaks during
    # that exit (pool workers being joined, atexit handlers) must not land on
    # the stdout the platform parses.
    proc = _entry("Lingering", LOGS, tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert _strict(proc.stdout)["text"] == "ok"  # nothing after it
    assert "POOL-NOISE" in proc.stderr and "POOL-CHILD" in proc.stderr
    assert "ATEXIT-NOISE" in proc.stderr


def test_stdout_is_given_back_after_a_call_that_finished_in_time(tmp_path):
    # The channel points fd 1 at stderr for the duration; a call that was not
    # abandoned must leave it as it found it, or the next answer in the same
    # process lands on stderr.
    program = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from backends import Remote; from job_tool.cli import main;"
        "main([], backend=Remote); main(['jobs'], backend=Remote)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", program, str(TESTS)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )

    first, second = proc.stdout.splitlines()
    assert [c["name"] for c in _strict(first)][:2] == ["run", "jobs"]
    assert _strict(second)["name"] == "jobs"


def test_what_the_backend_prints_never_reaches_the_answer(capsys):
    code = main(LOGS, backend=Chatty)
    out, err = capsys.readouterr()

    assert code == 0
    assert _strict(out)["text"] == "ok"
    assert "debug: reading" in err


def test_what_the_backend_prints_when_it_is_imported_never_reaches_the_answer(capsys, monkeypatch):
    # A module-level print or a library banner runs when the backend is
    # loaded — before any call, in stages 1 and 2 too, whose JSON the builder
    # and the platform parse.
    def noisy_load():
        print("acme-sdk v2.1 loaded")
        return Remote

    monkeypatch.setattr("job_tool.cli.load_backend", noisy_load)

    assert main([]) == 0
    out, err = capsys.readouterr()
    assert _strict(out)[0]["name"] == "run"
    assert "acme-sdk" in err


def test_a_backend_that_tries_to_exit_is_a_failure_not_a_silent_success(capsys):
    code = main(LOGS, backend=Exiting)
    out, err = capsys.readouterr()

    assert (code, out) == (1, "")
    assert "SystemExit" in err


def test_detail_the_backend_could_not_spell_as_json_still_makes_a_json_answer(capsys, tmp_path):
    # The call succeeded; reporting it as a failure would make the model redo it.
    from datetime import UTC, datetime

    from job_contract import Capacity, Worker

    class Odd(RemoteWithQueue):
        def capacity(self):
            free = {"since": datetime(2026, 11, 20, tzinfo=UTC), "load": math.nan}
            return Capacity(workers=(Worker("gpu-1", "idle", free=free),))

    code = main(["queue", "{}"], backend=Odd)
    out, _ = capsys.readouterr()

    assert code == 0
    free = _strict(out)["workers"][0]["free"]
    assert free["since"].startswith("2026-11-20")
    assert free["load"] == "nan"


def test_a_whole_number_written_with_a_point_is_the_same_whole_number(capsys, tmp_path):
    # JSON Schema counts 2.0 as an integer, so it passes validation; left a
    # float, the same request would hash differently (two jobs, two bills) and
    # break a backend that ranges with it.
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "run.sh").write_bytes(b"x")

    main(["run", json.dumps({"workdir": "train", "resources": {"gpus": 2.0}})], backend=Remote)
    first = _strict(capsys.readouterr().out)
    main(["run", json.dumps({"workdir": "train", "resources": {"gpus": 2}})], backend=Remote)
    again = _strict(capsys.readouterr().out)

    assert again["job_id"] == first["job_id"]
    assert again["state"] == "already"
    (started,) = [c for c in Remote.machine.calls if c[0] == "start"]
    assert type(started[2]["gpus"]) is int
