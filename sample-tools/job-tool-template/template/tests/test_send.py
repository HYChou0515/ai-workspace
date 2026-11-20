"""`run`: send a folder to the other machine, over as many calls as it takes,
and start it once everything has arrived."""

from __future__ import annotations

import json
import os

import pytest
from backends import Machine, Remote

from job_contract import NeedsAction, Retryable, Status
from job_tool import run as run_module
from job_tool.run import run

ITEM = "item-1"


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the working directory is the workspace
    monkeypatch.setenv("WORKSPACE_ITEM_ID", ITEM)
    Remote.machine = Machine()
    return tmp_path


def _folder(workspace, files=None, name="train"):
    files = (
        {"run.sh": b"python train.py\n", "train.py": b"print('hi')\n"} if files is None else files
    )
    for rel, data in files.items():
        path = workspace / name / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return name


def _send(workdir="train", resources=None):
    return run("run", {"workdir": workdir, "resources": resources or {"gpus": 1}}, Remote)


def _record(workspace, job_id):
    return json.loads((workspace / "jobs" / job_id / "job.json").read_text())


# ── one call ─────────────────────────────────────────────────────────────────


def test_a_small_folder_is_sent_and_started_in_one_call(workspace):
    _folder(workspace)

    outcome = _send(resources={"gpus": 2})

    m = Remote.machine
    job_id = outcome.answer["job_id"]
    assert outcome.exit_code == 0
    assert outcome.answer["state"] == "started"
    assert {p: bytes(d) for p, d in m.files[job_id].items()} == {
        "run.sh": b"python train.py\n",
        "train.py": b"print('hi')\n",
    }
    assert m.started == {job_id: {"gpus": 2}}
    assert "jobs" in outcome.answer["next"]


def test_the_job_id_is_one_every_backend_can_use_as_a_name(workspace):
    # A k8s name, a Slurm job name, a folder: lowercase letters, digits, '-'.
    _folder(workspace)

    job_id = _send().answer["job_id"]

    assert job_id.endswith("-1")
    assert len(job_id) <= 40
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in job_id)


def test_the_job_is_recorded_in_the_workspace_where_the_person_can_see_it(workspace):
    _folder(workspace)

    job_id = _send(resources={"gpus": 1}).answer["job_id"]

    record = _record(workspace, job_id)
    assert record["job_id"] == job_id
    assert record["workdir"] == "train"
    assert record["resources"] == {"gpus": 1}
    assert record["item_id"] == ITEM
    assert record["started"] is True
    assert record["files"] == 2


def test_the_records_folder_is_not_part_of_what_is_sent(workspace):
    _folder(workspace, {"run.sh": b"x"}, name=".")
    first = _send(workdir=".").answer["job_id"]

    # A second send of the same folder must not see the first one's record as
    # new content: it is the same job.
    again = _send(workdir=".").answer["job_id"]

    assert again == first
    assert set(Remote.machine.files[first]) == {"run.sh"}


def test_an_empty_file_arrives_too(workspace):
    _folder(workspace, {"run.sh": b"x", "data/empty.csv": b""})

    job_id = _send().answer["job_id"]

    assert bytes(Remote.machine.files[job_id]["data/empty.csv"]) == b""


def test_links_in_the_folder_are_named_as_not_sent(workspace):
    _folder(workspace)
    os.symlink("/etc/hostname", workspace / "train" / "host")

    outcome = _send()

    assert outcome.answer["not_sent"] == ["host"]


# ── many calls ───────────────────────────────────────────────────────────────


def test_a_folder_too_large_for_one_call_is_sent_over_several_and_started_once_complete(
    workspace, monkeypatch
):
    monkeypatch.setattr(run_module, "PIECE", 4)
    monkeypatch.setattr(run_module, "CALL_SECONDS", 1.6)  # three pieces, then too little left
    Remote.machine.delay["upload"] = 0.3
    data = bytes(range(40))
    _folder(workspace, {"run.sh": b"go", "blob.bin": data})

    first = _send()

    m = Remote.machine
    job_id = first.answer["job_id"]
    assert first.exit_code == 0
    assert first.answer["state"] == "uploading"
    assert 0 < first.answer["uploaded"] < first.answer["total"] == 42
    assert "run again" in first.answer["next"]
    assert m.count("start") == 0
    assert _record(workspace, job_id)["started"] is False

    calls = 0
    outcome = first
    while outcome.answer["state"] == "uploading":
        calls += 1
        assert calls < 20, "never finished"
        outcome = _send()
        assert outcome.answer["job_id"] == job_id

    assert outcome.answer["state"] == "started"
    assert bytes(m.files[job_id]["blob.bin"]) == data
    assert m.count("start") == 1


def test_a_resumed_upload_starts_where_the_other_side_says_it_stopped(workspace, monkeypatch):
    # D14: progress comes from the backend's sizes, never from the tool's memory.
    monkeypatch.setattr(run_module, "PIECE", 4)
    data = b"0123456789"
    _folder(workspace, {"blob.bin": data})
    job_id = _send().answer["job_id"]
    Remote.machine = Machine()  # start over: the other side holds the first 6 bytes only
    Remote.machine.files[job_id] = {"blob.bin": bytearray(data[:6])}
    # the record says nothing was started
    record_path = workspace / "jobs" / job_id / "job.json"
    record = json.loads(record_path.read_text())
    record["started"] = False
    record_path.write_text(json.dumps(record))

    _send()

    uploads = [c for c in Remote.machine.calls if c[0] == "upload"]
    assert [c[3] for c in uploads] == [6]  # offset 6, one piece of 4
    assert bytes(Remote.machine.files[job_id]["blob.bin"]) == data


def test_a_piece_that_never_arrived_is_sent_again_and_the_job_waits_for_it(workspace):
    _folder(workspace)
    Remote.machine.drop_pieces = 1

    first = _send()

    assert first.answer["state"] == "uploading"
    assert Remote.machine.count("start") == 0

    second = _send()

    assert second.answer["state"] == "started"
    assert len(Remote.machine.files[second.answer["job_id"]]) == 2


def test_the_other_side_holding_more_than_the_file_is_a_failure_not_a_start(workspace):
    _folder(workspace, {"run.sh": b"go"})
    job_id = _send().answer["job_id"]
    Remote.machine = Machine()
    Remote.machine.files[job_id] = {"run.sh": bytearray(b"go and more")}
    record_path = workspace / "jobs" / job_id / "job.json"
    record = json.loads(record_path.read_text())
    record["started"] = False
    record_path.write_text(json.dumps(record))

    outcome = _send()

    assert outcome.exit_code == 1
    assert "run.sh" in outcome.error
    assert Remote.machine.count("start") == 0


# ── the same job ─────────────────────────────────────────────────────────────


def test_the_same_folder_and_resources_while_the_job_is_unfinished_is_that_job(workspace):
    _folder(workspace)
    first = _send().answer["job_id"]
    Remote.machine.calls.clear()

    again = _send()

    assert again.exit_code == 0
    assert again.answer["job_id"] == first
    assert again.answer["state"] == "already"
    assert again.answer["status"]["phase"] == "queued"
    assert Remote.machine.count("upload") == Remote.machine.count("start") == 0


def test_the_same_request_after_the_job_finished_is_a_new_job(workspace):
    _folder(workspace)
    first = _send().answer["job_id"]
    Remote.machine.statuses[first] = Status("succeeded")

    again = _send()

    assert again.answer["state"] == "started"
    assert again.answer["job_id"] != first
    assert again.answer["job_id"].endswith("-2")
    assert _record(workspace, first)["job_id"] == first  # the old record stays


def test_other_resources_are_another_job(workspace):
    _folder(workspace)

    first = _send(resources={"gpus": 1}).answer["job_id"]
    other = _send(resources={"gpus": 2}).answer["job_id"]

    assert first != other


def test_a_changed_file_is_another_job(workspace):
    _folder(workspace)
    first = _send().answer["job_id"]
    (workspace / "train" / "train.py").write_bytes(b"print('changed')\n")

    assert _send().answer["job_id"] != first


def test_an_unfinished_job_whose_status_cannot_be_read_is_still_not_sent_twice(workspace):
    _folder(workspace)
    first = _send().answer["job_id"]
    Remote.machine.raises["status"] = Retryable("scheduler busy")

    again = _send()

    assert again.answer["job_id"] == first
    assert again.answer["state"] == "already"
    assert Remote.machine.count("start") == 1


def test_without_an_item_the_folder_alone_decides(workspace, monkeypatch):
    monkeypatch.delenv("WORKSPACE_ITEM_ID")
    _folder(workspace)

    outcome = _send()

    assert outcome.answer["state"] == "started"
    assert _record(workspace, outcome.answer["job_id"])["item_id"] is None


# ── start ────────────────────────────────────────────────────────────────────


def test_a_slow_start_that_landed_is_reported_as_started(workspace):
    _folder(workspace)
    Remote.machine.linger["start"] = 1.0  # budget 0.5; the start lands, its answer is late

    outcome = _send()

    assert outcome.exit_code == 0
    assert outcome.answer["state"] == "started"


def test_a_slow_start_that_did_not_land_is_safe_to_send_again(workspace):
    _folder(workspace)
    Remote.machine.delay["start"] = 1.0
    Remote.machine.raises["start"] = Retryable("never reached the scheduler")

    outcome = _send()

    assert outcome.exit_code == 2
    assert "again" in outcome.error
    record = json.loads(next((workspace / "jobs").glob("*/job.json")).read_text())
    assert record["started"] is False


def test_a_start_refused_for_quota_says_why_and_starts_nothing(workspace):
    _folder(workspace)
    Remote.machine.raises["start"] = Retryable("4 of your 4 jobs are running; wait for one")

    outcome = _send()

    assert outcome.exit_code == 2
    assert "4 of your 4" in outcome.error


def test_a_credential_the_tool_lacks_is_named_for_a_person_to_set(workspace):
    _folder(workspace)
    Remote.machine.raises["list_files"] = NeedsAction("set CLUSTER_TOKEN")

    outcome = _send()

    assert outcome.exit_code == 3
    assert "CLUSTER_TOKEN" in outcome.error


# ── the folder ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("workdir", ["/etc", "../outside", "train/../../x", "missing", "jobs"])
def test_a_folder_outside_the_workspace_missing_or_the_records_is_refused(workspace, workdir):
    _folder(workspace)
    (workspace / "jobs").mkdir(exist_ok=True)

    outcome = _send(workdir=workdir)

    assert outcome.exit_code == 2
    assert Remote.machine.calls == []


def test_a_folder_reached_through_a_link_outside_the_workspace_is_refused(
    workspace, tmp_path_factory
):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "run.sh").write_bytes(b"x")
    os.symlink(outside, workspace / "away")

    outcome = _send(workdir="away")

    assert outcome.exit_code == 2
    assert Remote.machine.calls == []


def test_a_folder_with_nothing_to_send_is_refused(workspace):
    (workspace / "empty" / "__pycache__").mkdir(parents=True)

    outcome = _send(workdir="empty")

    assert outcome.exit_code == 2
    assert "nothing" in outcome.error
