"""The demo backend keeps the promises the contract asks of every backend.

It is a demo — these tests exist so the template has a backend that really
runs, not to prescribe how yours should be tested.
"""

from __future__ import annotations

import pytest

from example_jobs.backend import DemoBackend
from job_contract import JobError, NeedsAction, NotFound, Retryable


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the working directory is the workspace
    monkeypatch.setenv("DEMO_JOBS_USER", "ann")
    return DemoBackend()


ITEM = "item-1"


def _key(spec, item=ITEM):
    return DemoBackend.contract.idempotency.key(spec, item_id=item)


def _send(backend, spec, item=ITEM):
    """What the shell does: key by item, then submit with the item."""
    return backend.submit(spec, _key(spec, item), item)


def test_the_item_a_job_came_from_is_recorded_and_reported(backend):
    job_id = _send(backend, {"task": "train", "seconds": 600})

    assert backend.get(job_id).item_id == ITEM
    assert [job.item_id for job in backend.list(None, 10)[0]] == [ITEM]


def test_two_credentials_sending_the_same_work_from_one_item_do_not_overwrite_each_other(
    backend, monkeypatch
):
    # The key no longer carries who sent it; the job id must, or the second
    # credential's record would replace the first's.
    spec = {"task": "train", "seconds": 600}
    anns = _send(backend, spec)
    monkeypatch.setenv("DEMO_JOBS_USER", "bob")
    bobs = _send(backend, spec)

    assert anns != bobs
    monkeypatch.setenv("DEMO_JOBS_USER", "ann")
    assert backend.get(anns).item_id == ITEM


def test_the_same_work_from_two_items_is_two_jobs_and_the_list_shows_both(backend):
    # D41 + D42: sameness is per item; what the credential sees is not.
    spec = {"task": "train", "seconds": 600}
    here, there = _send(backend, spec, "item-1"), _send(backend, spec, "item-2")

    assert here != there
    assert {job.item_id for job in backend.list(None, 10)[0]} == {"item-1", "item-2"}


def test_the_same_key_twice_is_one_job(backend):
    spec = {"task": "train", "seconds": 60}

    first = _send(backend, spec)
    again = _send(backend, spec)

    assert first == again
    jobs, _ = backend.list(None, 10)
    assert [job.job_id for job in jobs] == [first]
    assert backend.find_by_key(_key(spec)).job_id == first


def test_a_job_runs_then_finishes_and_its_result_can_be_fetched(backend, tmp_path):
    spec = {"task": "train", "seconds": 0}
    job_id = _send(backend, spec)

    status = backend.get(job_id)
    assert (status.phase, status.state) == ("done", "succeeded")
    assert backend.fetch(job_id, "result", "out/result.txt") == "out/result.txt"
    assert "train" in (tmp_path / "out/result.txt").read_text()


def test_a_cancelled_job_is_done_and_cancelling_it_again_is_not_an_error(backend):
    spec = {"task": "train", "seconds": 600}
    job_id = _send(backend, spec)

    with pytest.raises(Retryable):  # not yet — later it will be there
        backend.fetch(job_id, "result", "r.txt")

    assert backend.cancel(job_id).state == "cancelled"
    assert backend.cancel(job_id).phase == "done"

    # Never — asking again cannot help, so it must not say it might.
    with pytest.raises(JobError) as never:
        backend.fetch(job_id, "result", "r.txt")
    assert not isinstance(never.value, Retryable)


def test_a_cancelled_jobs_log_shows_only_what_it_ran(backend):
    spec = {"task": "train", "seconds": 100}
    job_id = _send(backend, spec)
    backend.cancel(job_id)

    log, total = backend.logs(job_id, 0, 10_000)

    assert "second 100" not in log
    assert log.endswith("cancelled\n") and total == len(log)


def test_changing_a_finished_job_is_a_plain_failure_the_model_can_act_on(backend):
    # Submitting a new job is something the model can do itself: not exit 3,
    # which tells it a person must act first.
    spec = {"task": "train", "seconds": 0}
    job_id = _send(backend, spec)

    with pytest.raises(JobError) as refused:
        backend.update(job_id, {"seconds": 10})
    assert not isinstance(refused.value, NeedsAction | Retryable)


@pytest.mark.parametrize("cursor", ["abc", "-1"])
def test_a_cursor_this_backend_never_gave_is_refused_so_it_can_be_corrected(backend, cursor):
    with pytest.raises(Retryable, match="cursor"):
        backend.list(cursor, 10)


def test_another_callers_job_does_not_exist_for_you(backend, monkeypatch):
    spec = {"task": "train", "seconds": 600}
    job_id = _send(backend, spec)

    monkeypatch.setenv("DEMO_JOBS_USER", "bob")
    with pytest.raises(NotFound):
        backend.get(job_id)
    assert backend.list(None, 10) == ([], None)


def test_no_user_set_is_a_person_s_job_naming_the_variable(backend, monkeypatch):
    monkeypatch.delenv("DEMO_JOBS_USER")
    with pytest.raises(NeedsAction, match="DEMO_JOBS_USER"):
        backend.list(None, 10)
