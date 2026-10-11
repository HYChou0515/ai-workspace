"""What leaves the process: stdout holds exactly the answer, the exit code is
the one the outcome calls for, and the process ends when the answer is given."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from backends import Scripted

from job_contract import JobStatus
from job_tool.cli import main

TESTS = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def fresh_script():
    Scripted.answers = {"caller_id": "ann"}
    Scripted.delay = {}
    Scripted.calls = []


def _strict(text: str):
    """json.loads that refuses NaN/Infinity, which are not JSON."""

    def refuse(token):
        raise ValueError(f"not JSON: {token}")

    return json.loads(text, parse_constant=refuse)


def test_the_process_ends_at_the_budget_even_when_the_backend_waits_on_a_pool():
    # The interpreter joins pool workers at exit; abandoning the call is not
    # enough unless the process also refuses to wait for them.
    program = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from backends import Pooled; from job_tool.cli import entry;"
        "raise SystemExit(entry(['get', '{\"job_id\": \"j1\"}'], backend=Pooled))"
    )
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", program, str(TESTS)], capture_output=True, text=True, timeout=60
    )
    took = time.monotonic() - started

    assert proc.returncode == 2, proc.stderr
    assert "0.2s" in proc.stderr
    assert took < 10  # the pool sleeps 30


def test_what_the_backend_prints_never_reaches_the_answer(capsys):
    class Chatty(Scripted):
        def get(self, job_id):
            print("debug: fetching", job_id)
            return JobStatus(job_id=job_id, phase="running", state="queued")

    code = main(["get", json.dumps({"job_id": "j1"})], backend=Chatty)
    out, err = capsys.readouterr()

    assert code == 0
    assert _strict(out)["job"]["job_id"] == "j1"
    assert "debug: fetching" in err


def test_a_backend_that_tries_to_exit_is_a_failure_not_a_silent_success(capsys):
    Scripted.answers["get"] = SystemExit(0)

    code = main(["get", json.dumps({"job_id": "j1"})], backend=Scripted)
    out, err = capsys.readouterr()

    assert (code, out) == (1, "")
    assert "SystemExit" in err


def test_detail_the_backend_could_not_spell_as_json_still_makes_a_json_answer(capsys):
    # The call succeeded; reporting it as a failure would make the model redo it.
    Scripted.answers["get"] = JobStatus(
        job_id="j1",
        phase="running",
        state="queued",
        detail={"at": datetime(2026, 10, 12, tzinfo=UTC), "loss": math.nan},
    )

    code = main(["get", json.dumps({"job_id": "j1"})], backend=Scripted)
    out, _ = capsys.readouterr()

    assert code == 0
    detail = _strict(out)["job"]["detail"]
    assert detail["at"].startswith("2026-10-12")
    assert detail["loss"] == "nan"


def test_a_whole_number_written_with_a_point_is_the_same_whole_number(capsys):
    # JSON Schema counts 5.0 as an integer, so it passes validation; left a
    # float, it gives the same spec a different key (two jobs, two bills) and
    # breaks a backend that slices or ranges with it.
    Scripted.answers["submit"] = "j1"
    spec = {"image": "trainer:3", "command": "train.py", "gpus": 2.0, "lr": 0.5}

    assert main(["submit", json.dumps(spec)], backend=Scripted) == 0
    assert main(["submit", json.dumps({**spec, "gpus": 2})], backend=Scripted) == 0
    capsys.readouterr()

    (first, second) = [call for call in Scripted.calls if call[0] == "submit"]
    assert first == second  # same spec, same key
    sent = first[1]
    assert type(sent["gpus"]) is int and sent["lr"] == 0.5


def test_more_arguments_than_one_json_object_are_refused(capsys):
    code = main(["get", '{"job_id": "x"}', '{"job_id": "y"}'], backend=Scripted)
    _, err = capsys.readouterr()

    assert code == 2
    assert "one" in err
    assert Scripted.calls == []
