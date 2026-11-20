"""Asking about jobs: `jobs`, `queue`, `files`, `fetch`, `logs`, `cancel`."""

from __future__ import annotations

import json
import os

import pytest
from backends import Machine, Remote, RemoteWithQueue, sample_capacity

from job_contract import NotFound, Retryable, Status
from job_tool import run as run_module
from job_tool.ask import FILES_MAX
from job_tool.commands import LOG_PAGE
from job_tool.run import run


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKSPACE_ITEM_ID", "item-1")
    Remote.machine = Machine()
    return tmp_path


def _sent(workspace, name="train", gpus=1, backend=Remote):
    folder = workspace / name
    folder.mkdir(exist_ok=True)
    (folder / "run.sh").write_bytes(f"echo {name}\n".encode())
    outcome = run("run", {"workdir": name, "resources": {"gpus": gpus}}, backend)
    assert outcome.answer["state"] == "started", outcome
    return outcome.answer["job_id"]


def _ask(name, args=None, backend=Remote):
    return run(name, args or {}, backend)


# ── jobs ─────────────────────────────────────────────────────────────────────


def test_jobs_lists_this_workspaces_jobs_newest_first_with_where_each_is_now(workspace):
    first = _sent(workspace, "a")
    second = _sent(workspace, "b")
    Remote.machine.statuses[first] = Status("running", worker="gpu-2", eta="11:00")

    outcome = _ask("jobs")

    listed = outcome.answer["jobs"]
    assert outcome.exit_code == 0
    assert [j["job_id"] for j in listed] == [second, first]
    assert listed[1]["workdir"] == "a"
    assert listed[1]["resources"] == {"gpus": 1}
    assert listed[1]["status"]["worker"] == "gpu-2"
    assert listed[0]["status"]["position"] == 3


def test_jobs_keeps_the_last_status_in_the_record(workspace):
    job = _sent(workspace)
    Remote.machine.statuses[job] = Status("running", state="epoch 3")

    _ask("jobs")

    record = json.loads((workspace / "jobs" / job / "job.json").read_text())
    assert record["last"]["state"] == "epoch 3"


def test_a_finished_job_is_not_asked_about_again(workspace):
    # Nothing about it changes any more; asking costs a call every time.
    job = _sent(workspace)
    Remote.machine.statuses[job] = Status("succeeded")
    _ask("jobs")
    Remote.machine.calls.clear()

    outcome = _ask("jobs")

    assert outcome.answer["jobs"][0]["status"]["phase"] == "succeeded"
    assert Remote.machine.count("status") == 0


def test_a_finished_job_points_at_files_fetch_and_logs(workspace):
    job = _sent(workspace)
    Remote.machine.statuses[job] = Status("failed", state="OOM killed")

    advice = _ask("jobs").answer["next"]

    assert "files" in advice and "fetch" in advice and "logs" in advice


def test_a_job_still_uploading_says_how_to_finish_sending_it(workspace, monkeypatch):
    monkeypatch.setattr(run_module, "CALL_SECONDS", 0.1)  # no time for a single piece
    (workspace / "big").mkdir()
    (workspace / "big" / "run.sh").write_bytes(b"x")
    pending = run("run", {"workdir": "big", "resources": {"gpus": 3}}, Remote)
    assert pending.answer["state"] == "uploading"
    monkeypatch.setattr(run_module, "CALL_SECONDS", 55)
    Remote.machine.calls.clear()

    listed = _ask("jobs").answer["jobs"][0]

    assert listed["state"] == "uploading"
    assert listed["status"] is None
    assert "run" in listed["next"] and "big" in listed["next"] and '"gpus": 3' in listed["next"]
    assert Remote.machine.count("status") == 0


def test_a_job_the_backend_no_longer_has_is_said_so_not_a_failure_of_jobs(workspace):
    job = _sent(workspace)
    del Remote.machine.started[job]

    outcome = _ask("jobs")

    assert outcome.exit_code == 0
    status = outcome.answer["jobs"][0]["status"]
    assert status["phase"] == "unknown"
    assert "no longer" in status["state"]


def test_a_status_that_cannot_be_read_is_unknown_and_never_gone(workspace):
    _sent(workspace)
    Remote.machine.raises["status"] = Retryable("scheduler down")

    status = _ask("jobs").answer["jobs"][0]["status"]

    assert status["phase"] == "unknown"
    assert "scheduler down" in status["state"]


def test_jobs_stops_asking_while_there_is_still_time_to_answer(workspace, monkeypatch):
    for name in "abc":
        _sent(workspace, name)
    Remote.machine.delay["status"] = 0.3
    monkeypatch.setattr(run_module, "CALL_SECONDS", 1.0)  # budget 0.5: one ask fits

    outcome = _ask("jobs")

    assert outcome.exit_code == 0
    unchecked = [j for j in outcome.answer["jobs"] if j["status"] is None]
    assert unchecked and all("not checked" in j["next"] for j in unchecked)
    assert Remote.machine.count("status") < 3


def test_jobs_shows_the_newest_few_and_says_how_many_more(workspace):
    for name in "abc":
        _sent(workspace, name)

    outcome = _ask("jobs", {"limit": 2})

    assert len(outcome.answer["jobs"]) == 2
    assert outcome.answer["more"] == 1


def test_with_no_jobs_jobs_says_none_were_sent_from_here(workspace):
    outcome = _ask("jobs")

    assert outcome.answer["jobs"] == []
    assert "run" in outcome.answer["next"]


# ── queue ────────────────────────────────────────────────────────────────────


def test_queue_shows_workers_the_queue_and_the_quota(workspace):
    RemoteWithQueue.machine.capacity = sample_capacity()

    answer = _ask("queue", backend=RemoteWithQueue).answer

    assert [w["name"] for w in answer["workers"]] == ["gpu-1", "gpu-2"]
    assert answer["workers"][1]["free"] == {"gpus": 8}
    assert answer["queue"][0]["job_id"] == "j-other-2"
    assert answer["queue"][0]["mine"] is False
    assert answer["quota"] == {"gpu_hours": "12 of 20"}


def test_queue_marks_the_jobs_this_workspace_sent(workspace):
    from job_contract import Capacity, QueuedJob

    job = _sent(workspace, backend=RemoteWithQueue)
    RemoteWithQueue.machine.capacity = Capacity(queue=(QueuedJob(job), QueuedJob("x")))

    queue = _ask("queue", backend=RemoteWithQueue).answer["queue"]

    assert [q["mine"] for q in queue] == [True, False]


# ── files ────────────────────────────────────────────────────────────────────


def test_files_lists_what_the_job_has_on_the_other_side(workspace):
    job = _sent(workspace)
    Remote.machine.files[job]["out/model.pt"] = bytearray(b"m" * 10)

    answer = _ask("files", {"job_id": job}).answer

    assert {"path": "out/model.pt", "is_dir": False, "size": 10} in [
        {k: e[k] for k in ("path", "is_dir", "size")} for e in answer["entries"]
    ]
    assert "fetch" in answer["next"]


def test_files_under_a_path(workspace):
    job = _sent(workspace)
    Remote.machine.files[job]["out/model.pt"] = bytearray(b"m")

    entries = _ask("files", {"job_id": job, "path": "out"}).answer["entries"]

    assert [e["path"] for e in entries] == ["out/model.pt"]


@pytest.mark.parametrize("path", ["/etc", "../x", "out/../../y"])
def test_files_outside_the_jobs_folder_are_refused(workspace, path):
    job = _sent(workspace)
    Remote.machine.calls.clear()

    outcome = _ask("files", {"job_id": job, "path": path})

    assert outcome.exit_code == 2
    assert Remote.machine.calls == []


def test_a_long_listing_is_cut_and_says_how_much_more(workspace):
    job = _sent(workspace)
    for i in range(FILES_MAX + 5):
        Remote.machine.files[job][f"out/{i:04}.png"] = bytearray(b"x")

    answer = _ask("files", {"job_id": job}).answer

    assert len(answer["entries"]) == FILES_MAX
    assert answer["more"] > 0
    assert "path" in answer["next"]


def test_files_of_a_job_the_backend_does_not_have_is_a_plain_failure(workspace):
    outcome = _ask("files", {"job_id": "j-nope-1"})

    assert outcome.exit_code == 1


# ── fetch ────────────────────────────────────────────────────────────────────


def test_fetch_copies_a_file_into_the_jobs_folder_in_the_workspace(workspace):
    job = _sent(workspace)
    Remote.machine.files[job]["out/model.pt"] = bytearray(b"weights")

    answer = _ask("fetch", {"job_id": job, "path": "out/model.pt"}).answer

    saved = workspace / "jobs" / job / "fetched" / "out" / "model.pt"
    assert saved.read_bytes() == b"weights"
    assert answer["state"] == "done"
    assert answer["saved"] == [f"jobs/{job}/fetched/out/model.pt"]


def test_fetch_copies_a_whole_folder_to_where_it_is_told(workspace):
    job = _sent(workspace)
    Remote.machine.files[job]["out/a.txt"] = bytearray(b"a")
    Remote.machine.files[job]["out/sub/b.txt"] = bytearray(b"b")

    _ask("fetch", {"job_id": job, "path": "out", "dest": "results"})

    assert (workspace / "results" / "a.txt").read_bytes() == b"a"
    assert (workspace / "results" / "sub" / "b.txt").read_bytes() == b"b"


def test_a_large_fetch_continues_where_it_stopped(workspace, monkeypatch):
    job = _sent(workspace)
    data = bytes(range(50))
    Remote.machine.files[job]["big.bin"] = bytearray(data)
    monkeypatch.setattr(run_module, "PIECE", 8)
    monkeypatch.setattr(run_module, "CALL_SECONDS", 1.6)
    Remote.machine.delay["download"] = 0.3

    first = _ask("fetch", {"job_id": job, "path": "big.bin", "dest": "big.bin"}).answer
    assert first["state"] == "partial"
    assert "fetch again" in first["next"]
    assert not (workspace / "big.bin").exists()

    offsets = []
    answer = first
    for _ in range(20):
        if answer["state"] == "done":
            break
        Remote.machine.calls.clear()
        answer = _ask("fetch", {"job_id": job, "path": "big.bin", "dest": "big.bin"}).answer
        offsets += [c[3] for c in Remote.machine.calls if c[0] == "download"]

    assert answer["state"] == "done"
    assert (workspace / "big.bin").read_bytes() == data
    assert 0 not in offsets  # every later call resumed; none began again


def test_a_file_already_fetched_whole_is_not_fetched_again(workspace):
    job = _sent(workspace)
    Remote.machine.files[job]["a.txt"] = bytearray(b"a")
    _ask("fetch", {"job_id": job, "path": "a.txt", "dest": "a.txt"})
    Remote.machine.calls.clear()

    _ask("fetch", {"job_id": job, "path": "a.txt", "dest": "a.txt"})

    assert Remote.machine.count("download") == 0


@pytest.mark.parametrize("dest", ["/tmp/out", "../out", "a/../../out", ".", "away/x"])
def test_fetch_never_writes_outside_the_workspace(workspace, dest, tmp_path_factory):
    os.symlink(tmp_path_factory.mktemp("outside"), workspace / "away")
    job = _sent(workspace)
    Remote.machine.files[job]["a.txt"] = bytearray(b"a")
    Remote.machine.calls.clear()

    outcome = _ask("fetch", {"job_id": job, "path": "a.txt", "dest": dest})

    assert outcome.exit_code == 2
    assert Remote.machine.count("download") == 0


def test_fetching_what_is_not_there_is_a_plain_failure(workspace):
    job = _sent(workspace)

    outcome = _ask("fetch", {"job_id": job, "path": "nope.txt"})

    assert outcome.exit_code == 1
    assert "nope.txt" in outcome.error


def test_a_file_that_ends_early_on_the_other_side_is_a_failure_not_a_short_copy(workspace):
    job = _sent(workspace)
    Remote.machine.files[job]["a.txt"] = bytearray(b"abc")

    class Shrinking(Remote):
        def download(self, job_id, path, offset, limit):
            return b""

    outcome = _ask("fetch", {"job_id": job, "path": "a.txt", "dest": "a.txt"}, backend=Shrinking)

    assert outcome.exit_code == 1
    assert not (workspace / "a.txt").exists()


# ── logs ─────────────────────────────────────────────────────────────────────


def test_logs_without_an_offset_reads_the_end(workspace):
    job = _sent(workspace)
    Remote.machine.logs[job] = "x" * 10 + "Traceback: OOM"

    answer = _ask("logs", {"job_id": job, "limit": 14}).answer

    assert answer["text"] == "Traceback: OOM"
    assert answer["offset"] == 10
    assert "offset" in answer["next"]


def test_logs_from_an_offset_say_how_much_is_left_and_where_to_read_it_from(workspace):
    job = _sent(workspace)
    Remote.machine.logs[job] = "abcdefghij"

    answer = _ask("logs", {"job_id": job, "offset": 2, "limit": 3}).answer

    assert answer["text"] == "cde"
    assert (answer["end"], answer["total"]) == (5, 10)
    assert "offset=5" in answer["next"]


def test_a_log_read_to_its_end_says_so(workspace):
    job = _sent(workspace)
    Remote.machine.logs[job] = "done"

    answer = _ask("logs", {"job_id": job}).answer

    assert answer["text"] == "done"
    assert "end" in answer["next"]


def test_the_default_log_page_is_bounded():
    assert 0 < LOG_PAGE <= 8000


# ── cancel ───────────────────────────────────────────────────────────────────


def test_cancel_reports_where_the_job_now_is(workspace):
    job = _sent(workspace)

    answer = _ask("cancel", {"job_id": job}).answer

    assert Remote.machine.cancelled == [job]
    assert answer["status"]["phase"] == "failed"
    record = json.loads((workspace / "jobs" / job / "job.json").read_text())
    assert record["last"]["state"] == "cancelled"


# ── every call ───────────────────────────────────────────────────────────────


def test_a_backend_that_overruns_its_budget_is_abandoned_at_the_budget(workspace):
    job = _sent(workspace)
    Remote.machine.delay["cancel"] = 30

    outcome = _ask("cancel", {"job_id": job})

    assert outcome.exit_code == 2
    assert "0.5s" in outcome.error


def test_a_backend_that_breaks_unexpectedly_is_a_plain_failure_not_a_traceback(workspace):
    job = _sent(workspace)
    Remote.machine.raises["logs"] = KeyError("boom")

    outcome = _ask("logs", {"job_id": job})

    assert outcome.exit_code == 1
    assert outcome.error == "the backend failed: KeyError: 'boom'"


def test_a_job_the_backend_is_certain_of_not_having_is_a_plain_failure(workspace):
    Remote.machine.raises["cancel"] = NotFound("no job j-x")

    outcome = _ask("cancel", {"job_id": "j-x"})

    assert outcome.exit_code == 1
    assert "no job j-x" in outcome.error
