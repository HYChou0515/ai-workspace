"""The demo backend keeps the promises the contract asks of every backend.

It is a demo — these tests exist so the template has a backend that really
runs, not to prescribe how yours should be tested.
"""

from __future__ import annotations

import time

import pytest

from example_jobs.backend import DemoBackend
from job_contract import NeedsAction, NotFound, Retryable


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_JOBS_USER", "ann")
    monkeypatch.setenv("DEMO_JOBS_ROOT", str(tmp_path / "machine"))
    DemoBackend.check()
    return DemoBackend()


def _send(backend, job_id, files):
    for path, data in files.items():
        backend.upload(job_id, path, 0, data)


def _finished(backend, job_id, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        status = backend.status(job_id)
        if status.finished:
            return status
        time.sleep(0.05)
    raise AssertionError(f"{job_id} did not finish within {seconds}s")


def test_an_upload_lands_at_its_offset_and_never_truncates(backend):
    backend.upload("j1", "data/a.bin", 0, b"abcd")
    backend.upload("j1", "data/a.bin", 4, b"efgh")
    backend.upload("j1", "data/a.bin", 0, b"abcd")  # the same piece again: harmless

    (entry,) = [e for e in backend.list_files("j1", ".") if not e.is_dir]
    assert (entry.path, entry.size) == ("data/a.bin", 8)
    assert backend.download("j1", "data/a.bin", 0, 100) == b"abcdefgh"


def test_what_arrived_is_listed_before_the_job_starts(backend):
    backend.upload("j1", "run.sh", 0, b"echo hi\n")

    assert [(e.path, e.size) for e in backend.list_files("j1", ".")] == [("run.sh", 8)]


def test_a_job_with_no_folder_yet_is_not_found(backend):
    with pytest.raises(NotFound):
        backend.list_files("j1", ".")
    with pytest.raises(NotFound):
        backend.status("j1")


def test_a_job_runs_its_run_sh_and_what_it_writes_can_be_listed_and_read(backend):
    _send(backend, "j1", {"run.sh": b"echo working\nmkdir -p out && echo 42 > out/answer.txt\n"})

    backend.start("j1", {"cpus": 1})
    status = _finished(backend, "j1")

    assert status.phase == "succeeded"
    assert ("out/answer.txt", 3) in [(e.path, e.size) for e in backend.list_files("j1", "out")]
    assert backend.download("j1", "out/answer.txt", 0, 10) == b"42\n"
    text, total = backend.logs("j1", 0, 1000)
    assert "working" in text and total == len(text)


def test_starting_the_same_job_twice_runs_it_once(backend):
    _send(backend, "j1", {"run.sh": b"echo once >> count.txt\n"})

    backend.start("j1", {"cpus": 1})
    backend.start("j1", {"cpus": 1})
    _finished(backend, "j1")

    assert backend.download("j1", "count.txt", 0, 100) == b"once\n"


def test_the_folders_env_file_sets_the_jobs_variables(backend):
    _send(backend, "j1", {"run.sh": b'echo "$SEED" > seed.txt\n', ".env": b"SEED=7\n"})

    backend.start("j1", {"cpus": 1})
    _finished(backend, "j1")

    assert backend.download("j1", "seed.txt", 0, 10) == b"7\n"


def test_a_job_that_exits_with_an_error_failed_and_says_how(backend):
    _send(backend, "j1", {"run.sh": b"exit 3\n"})

    backend.start("j1", {"cpus": 1})
    status = _finished(backend, "j1")

    assert status.phase == "failed"
    assert "3" in status.state


def test_a_folder_without_run_sh_is_refused_at_start_naming_it(backend):
    _send(backend, "j1", {"train.py": b"print(1)\n"})

    with pytest.raises(Retryable, match="run.sh"):
        backend.start("j1", {"cpus": 1})


def test_a_cancelled_job_failed_and_says_it_was_cancelled(backend):
    _send(backend, "j1", {"run.sh": b"sleep 30\n"})
    backend.start("j1", {"cpus": 1})

    backend.cancel("j1")
    status = _finished(backend, "j1")

    assert status.phase == "failed"
    assert "cancel" in status.state


def test_cancelling_a_finished_job_is_not_an_error(backend):
    _send(backend, "j1", {"run.sh": b"true\n"})
    backend.start("j1", {"cpus": 1})
    _finished(backend, "j1")

    backend.cancel("j1")

    assert backend.status("j1").phase == "succeeded"


def test_a_running_job_is_running_and_the_machine_says_it_is_busy(backend):
    _send(backend, "j1", {"run.sh": b"sleep 30\n"})
    backend.start("j1", {"cpus": 2})
    try:
        assert backend.status("j1").phase == "running"
        (worker,) = backend.capacity().workers
        assert (worker.state, worker.job_id) == ("busy", "j1")
    finally:
        backend.cancel("j1")


def test_without_a_user_a_person_is_asked_to_set_one(backend, monkeypatch):
    monkeypatch.delenv("DEMO_JOBS_USER")

    with pytest.raises(NeedsAction, match="DEMO_JOBS_USER"):
        backend.list_files("j1", ".")


def test_another_users_job_does_not_exist_for_you(backend, monkeypatch):
    backend.upload("j1", "run.sh", 0, b"true\n")
    monkeypatch.setenv("DEMO_JOBS_USER", "bob")

    with pytest.raises(NotFound):
        backend.list_files("j1", ".")


def test_two_starts_at_once_run_the_job_once(backend):
    import threading

    _send(backend, "j1", {"run.sh": b"echo once >> count.txt\n"})
    threads = [threading.Thread(target=backend.start, args=("j1", {"cpus": 1})) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _finished(backend, "j1")

    assert backend.download("j1", "count.txt", 0, 100) == b"once\n"
