"""Running one call: what the model is told, and the exit code that tells it
what to do next."""

from __future__ import annotations

import json
import time

import pytest
from backends import Scripted, ScriptedFull

from job_contract import JobError, JobStatus, NeedsAction, NotFound, Retryable, Submitted
from job_tool.cli import main
from job_tool.commands import LIST_PAGE, LOG_PAGE


ITEM = "item-1"


@pytest.fixture(autouse=True)
def fresh_script(monkeypatch):
    Scripted.answers = {}
    Scripted.delay = {}
    Scripted.calls = []
    # As the platform sets it on every dispatch (D44).
    monkeypatch.setenv("WORKSPACE_ITEM_ID", ITEM)


def _run(capsys, name, args):
    code = main([name, json.dumps(args)], backend=Scripted)
    out, err = capsys.readouterr()
    return code, out, err


def test_a_running_job_is_reported_with_the_advice_to_ask_again_later(capsys):
    Scripted.answers["get"] = JobStatus(job_id="j1", phase="running", state="suspended")

    code, out, _ = _run(capsys, "get", {"job_id": "j1"})

    answer = json.loads(out)
    assert code == 0
    assert answer["job"] == {
        "job_id": "j1",
        "phase": "running",
        "state": "suspended",
        "detail": {},
        "item_id": None,
        "from_this_item": False,
    }
    assert "get" in answer["next"] and "later" in answer["next"]


def test_a_backend_that_overruns_its_budget_is_abandoned_at_the_budget(capsys):
    Scripted.answers["get"] = JobStatus(job_id="j1", phase="running", state="queued")
    Scripted.delay["get"] = 30.0  # budget is 0.2

    started = time.monotonic()
    code, out, err = _run(capsys, "get", {"job_id": "j1"})
    took = time.monotonic() - started

    # The budget, not the backend, decided when to answer. Far from both
    # numbers, so a slow machine cannot make this flip.
    assert took < 10
    assert code == 2
    assert out == ""
    assert "0.2s" in err and "again" in err


def test_a_backend_that_cannot_say_is_not_reported_as_a_job_that_is_gone(capsys):
    Scripted.answers["get"] = JobStatus(job_id="j1", phase="unknown", state="scheduler down")

    code, out, _ = _run(capsys, "get", {"job_id": "j1"})

    answer = json.loads(out)
    assert code == 2  # asking again may work
    assert answer["job"]["state"] == "scheduler down"
    assert "not" in answer["next"] and "exist" in answer["next"]


def test_a_job_the_backend_is_certain_of_not_having_is_a_plain_failure(capsys):
    Scripted.answers["get"] = NotFound("no job j9 for ann")

    code, out, err = _run(capsys, "get", {"job_id": "j9"})

    assert (code, out) == (1, "")
    assert "no job j9 for ann" in err


class WithLogs(Scripted):
    def logs(self, job_id, offset, limit):
        return "", 0


def test_a_finished_job_points_only_at_the_calls_the_backend_has(capsys):
    Scripted.answers["get"] = JobStatus(job_id="j1", phase="done", state="failed: OOM")

    _, out, _ = _run(capsys, "get", {"job_id": "j1"})
    assert json.loads(out)["next"] == "Finished; its state says how it ended."

    code = main(["get", json.dumps({"job_id": "j1"})], backend=WithLogs)
    out, _ = capsys.readouterr()
    assert code == 0
    assert "logs" in json.loads(out)["next"] and "fetch" not in json.loads(out)["next"]


def test_a_finished_job_is_not_promised_outputs_it_may_not_have(capsys):
    # The shell reads only `phase`: "done" may be a success or a cancel, so
    # fetch is offered as a possibility, never as the next step.
    Scripted.answers["cancel"] = JobStatus(job_id="j1", phase="done", state="cancelled")

    _, out, _ = _run_full(capsys, "cancel", {"job_id": "j1"})

    advice = json.loads(out)["next"]
    assert "state" in advice
    assert "if it produced any" in advice


def test_a_backend_that_breaks_unexpectedly_is_a_plain_failure_not_a_traceback(capsys):
    Scripted.answers["get"] = KeyError("job_id")

    code, out, err = _run(capsys, "get", {"job_id": "j1"})

    assert (code, out) == (1, "")
    assert "KeyError" in err and "Traceback" not in err


def test_arguments_outside_the_schema_never_reach_the_backend(capsys):
    code, out, err = _run(capsys, "submit", {"image": "trainer:3"})  # no `command`

    assert (code, out) == (2, "")  # the model can fix it and call again
    assert "command" in err
    assert Scripted.calls == []


SPEC_IN = {"image": "trainer:3", "command": "train.py"}


def test_submit_sends_the_key_the_contract_derives_for_this_item(capsys):
    Scripted.answers["submit"] = Submitted("j1", created=True)

    code, out, _ = _run(capsys, "submit", SPEC_IN)

    expected = Scripted.contract.idempotency.key(SPEC_IN, item_id=ITEM)
    assert ("submit", SPEC_IN, expected, ITEM) in Scripted.calls
    answer = json.loads(out)
    assert code == 0
    assert answer["job_id"] == "j1"
    # Nothing will call the user back (D10): the model has to say so.
    assert "get" in answer["next"] and "later" in answer["next"]


def test_a_submit_that_found_the_job_already_there_says_so(capsys):
    # D51: within the window the backend returns the job it already has; the
    # model must not tell the user a new one was started.
    Scripted.answers["submit"] = Submitted("j1", created=False)

    code, out, _ = _run(capsys, "submit", SPEC_IN)

    answer = json.loads(out)
    assert code == 0
    assert answer["job_id"] == "j1" and answer["created"] is False
    assert "already" in answer["next"] and "not a new" in answer["next"]


def test_a_submit_that_started_a_job_says_it_is_new(capsys):
    Scripted.answers["submit"] = Submitted("j1", created=True)

    _, out, _ = _run(capsys, "submit", SPEC_IN)

    assert json.loads(out)["created"] is True


def test_a_backend_that_answers_submit_with_a_bare_id_is_a_failure(capsys):
    # Not guessing "new": a wrong guess is exactly the false sentence D51 stops.
    Scripted.answers["submit"] = "j1"

    code, out, err = _run(capsys, "submit", SPEC_IN)

    assert (code, out) == (1, "")
    assert "Submitted" in err


def test_without_an_item_the_key_is_the_fields_alone(capsys, monkeypatch):
    # D47: the MCP runner sets no item. The tool still works.
    monkeypatch.delenv("WORKSPACE_ITEM_ID")
    Scripted.answers["submit"] = Submitted("j1", created=True)

    code, _, _ = _run(capsys, "submit", SPEC_IN)

    expected = Scripted.contract.idempotency.key(SPEC_IN, item_id=None)
    assert code == 0
    assert ("submit", SPEC_IN, expected, None) in Scripted.calls


def test_jobs_from_this_item_are_marked_and_the_rest_are_still_shown(capsys):
    # D42: the queue as the credential sees it — never filtered to the item —
    # with each job saying whether it came from here.
    Scripted.answers["list"] = (
        [
            JobStatus(job_id="mine", phase="running", state="q", item_id=ITEM),
            JobStatus(job_id="theirs", phase="running", state="q", item_id="item-2"),
            JobStatus(job_id="unknown", phase="done", state="ok"),
        ],
        None,
    )

    _, out, _ = _run(capsys, "list", {})

    marks = {j["job_id"]: j["from_this_item"] for j in json.loads(out)["jobs"]}
    assert marks == {"mine": True, "theirs": False, "unknown": False}


def test_get_says_whether_the_job_came_from_this_item(capsys):
    Scripted.answers["get"] = JobStatus(job_id="j1", phase="running", state="q", item_id=ITEM)

    _, out, _ = _run(capsys, "get", {"job_id": "j1"})

    assert json.loads(out)["job"]["from_this_item"] is True


def test_a_slow_submit_that_landed_is_reported_as_submitted(capsys):
    Scripted.answers["submit"] = Submitted("j1", created=True)
    Scripted.delay["submit"] = 30.0  # budget is 0.2
    Scripted.answers["find_by_key"] = JobStatus(job_id="j1", phase="running", state="queued")

    code, out, _ = _run(capsys, "submit", SPEC_IN)

    key = Scripted.contract.idempotency.key(SPEC_IN, item_id=ITEM)
    assert ("find_by_key", key) in Scripted.calls  # asked about THIS submit
    answer = json.loads(out)
    assert code == 0
    assert answer["job_id"] == "j1"


def test_a_slow_submit_that_did_not_land_may_be_sent_again(capsys):
    Scripted.answers["submit"] = Submitted("j1", created=True)
    Scripted.delay["submit"] = 30.0
    Scripted.answers["find_by_key"] = None

    code, out, err = _run(capsys, "submit", SPEC_IN)

    assert (code, out) == (2, "")
    # Safe because the same spec carries the same key: a late arrival of the
    # first one would be returned, not duplicated.
    assert "again" in err and "same" in err


@pytest.mark.parametrize(
    "lookup",
    [
        {"delay": 30.0},
        {"answer": JobError("scheduler said no")},
        # Not 2: the platform would tell the model to call again, while the
        # message says to call list first.
        {"answer": Retryable("scheduler busy")},
        # Not "the backend failed": this path exists to say "unknown".
        {"answer": SystemExit(0)},
    ],
    ids=["lookup-times-out", "lookup-fails", "lookup-retryable", "lookup-exits"],
)
def test_a_slow_submit_nobody_can_confirm_is_unknown_and_must_be_checked_first(capsys, lookup):
    Scripted.answers["submit"] = Submitted("j1", created=True)
    Scripted.delay["submit"] = 30.0
    Scripted.answers["find_by_key"] = lookup.get("answer")
    Scripted.delay["find_by_key"] = lookup.get("delay", 0)

    code, out, err = _run(capsys, "submit", SPEC_IN)

    assert (code, out) == (1, "")
    assert "unknown" in err and "list" in err
    if isinstance(lookup.get("answer"), SystemExit):
        assert "SystemExit" in err  # "(0)" alone would tell the model nothing


def test_a_lookup_that_needs_a_person_still_says_so(capsys):
    # The outcome is unknown either way, but exit 3 is what lets the platform
    # offer the person a way to fix it; flattening it to 1 loses that.
    Scripted.answers["submit"] = Submitted("j1", created=True)
    Scripted.delay["submit"] = 30.0
    Scripted.answers["find_by_key"] = NeedsAction("set GPU_API_TOKEN")

    code, _, err = _run(capsys, "submit", SPEC_IN)

    assert code == 3
    assert "unknown" in err and "GPU_API_TOKEN" in err


def test_a_submit_stuck_before_it_was_sent_is_safe_to_send_again(capsys):
    # Stuck building the backend (connecting to the scheduler): submit was not
    # called. Resending is safe because of the key, which is what the model
    # must be told — not that nothing could ever have been sent.
    Scripted.delay["__init__"] = 30.0

    code, _, err = _run(capsys, "submit", SPEC_IN)

    assert code == 2
    assert not any(call[0] in ("submit", "find_by_key") for call in Scripted.calls)
    assert "most likely not sent" in err and "same key" in err


def test_list_gives_a_page_and_says_whether_there_is_more(capsys):
    jobs = [JobStatus(job_id="j2", phase="running", state="queued")]
    Scripted.answers["list"] = (jobs, "page-2")

    code, out, _ = _run(capsys, "list", {})

    answer = json.loads(out)
    assert code == 0
    assert ("list", None, LIST_PAGE) in Scripted.calls
    assert [j["job_id"] for j in answer["jobs"]] == ["j2"]
    assert answer["next_cursor"] == "page-2" and "page-2" in answer["next"]

    Scripted.answers["list"] = ([], None)
    _, out, _ = _run(capsys, "list", {"cursor": "page-2", "limit": 5})
    assert json.loads(out)["next_cursor"] is None
    assert "no more" in json.loads(out)["next"]


def test_a_cursor_the_backend_gave_as_a_number_can_still_be_passed_back(capsys):
    # The shell's own schema says cursor is a string; handing the model 5
    # would get its next call refused, and page 2 would never be reached.
    Scripted.answers["list"] = ([], 5)
    _, out, _ = _run(capsys, "list", {})
    assert json.loads(out)["next_cursor"] == "5"

    code, _, _ = _run(capsys, "list", {"cursor": "5"})
    assert code == 0


def test_list_pages_are_bounded(capsys):
    code, _, err = _run(capsys, "list", {"limit": 10_000})
    assert code == 2 and "limit" in err


def _run_full(capsys, name, args):
    code = main([name, json.dumps(args)], backend=ScriptedFull)
    out, err = capsys.readouterr()
    return code, out, err


def test_an_update_is_reported_as_accepted_not_as_done(capsys):
    Scripted.answers["update"] = JobStatus(job_id="j1", phase="running", state="queued")

    code, out, _ = _run_full(capsys, "update", {"job_id": "j1", "patch": {"priority": 9}})

    answer = json.loads(out)
    assert code == 0
    assert ("update", "j1", {"priority": 9}) in Scripted.calls
    assert "accepted" in answer["next"] and "get" in answer["next"]


def test_a_cancel_is_reported_with_where_the_job_now_is(capsys):
    Scripted.answers["cancel"] = JobStatus(job_id="j1", phase="done", state="cancelled")

    code, out, _ = _run_full(capsys, "cancel", {"job_id": "j1"})

    answer = json.loads(out)
    assert code == 0
    assert answer["job"]["state"] == "cancelled"
    assert answer["next"].startswith("Cancel accepted.")


def test_logs_say_how_much_is_left_and_where_to_read_it_from(capsys):
    Scripted.answers["logs"] = ("epoch 1 done\n", 120)

    code, out, _ = _run_full(capsys, "logs", {"job_id": "j1"})

    answer = json.loads(out)
    assert code == 0
    assert ("logs", "j1", 0, LOG_PAGE) in Scripted.calls
    assert (answer["text"], answer["end"], answer["total"]) == ("epoch 1 done\n", 13, 120)
    assert "offset=13" in answer["next"]

    Scripted.answers["logs"] = ("tail\n", 18)
    _, out, _ = _run_full(capsys, "logs", {"job_id": "j1", "offset": 13})
    assert "offset" not in json.loads(out)["next"]


@pytest.mark.parametrize(
    "dest", ["/etc/passwd", "../outside.txt", "out/../../x", "", ".", "linked/x", "a\x00b"]
)
def test_fetch_never_writes_outside_the_workspace(capsys, dest, tmp_path, monkeypatch):
    workspace, elsewhere = tmp_path / "workspace", tmp_path / "elsewhere"
    workspace.mkdir()
    elsewhere.mkdir()
    (workspace / "linked").symlink_to(elsewhere)  # inside by name, outside in fact
    monkeypatch.chdir(workspace)

    code, _, err = _run_full(capsys, "fetch", {"job_id": "j1", "name": "model", "dest": dest})

    assert code == 2
    assert "inside the workspace" in err
    assert Scripted.calls == []


def test_fetch_reports_where_the_output_was_saved(capsys):
    Scripted.answers["fetch"] = "results/model.pt"

    code, out, _ = _run_full(
        capsys, "fetch", {"job_id": "j1", "name": "model", "dest": "results/model.pt"}
    )

    assert code == 0
    assert json.loads(out)["path"] == "results/model.pt"
