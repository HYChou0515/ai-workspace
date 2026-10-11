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


def _key(backend, spec):
    return DemoBackend.contract.idempotency.key(spec, caller=backend.caller_id())


def test_the_same_key_twice_is_one_job(backend):
    spec = {"task": "train", "seconds": 60}

    first = backend.submit(spec, _key(backend, spec))
    again = backend.submit(spec, _key(backend, spec))

    assert first == again
    jobs, _ = backend.list(None, 10)
    assert [job.job_id for job in jobs] == [first]
    assert backend.find_by_key(_key(backend, spec)).job_id == first


def test_a_job_runs_then_finishes_and_its_result_can_be_fetched(backend, tmp_path):
    spec = {"task": "train", "seconds": 0}
    job_id = backend.submit(spec, _key(backend, spec))

    status = backend.get(job_id)
    assert (status.phase, status.state) == ("done", "succeeded")
    assert backend.fetch(job_id, "result", "out/result.txt") == "out/result.txt"
    assert "train" in (tmp_path / "out/result.txt").read_text()


def test_a_cancelled_job_is_done_and_cancelling_it_again_is_not_an_error(backend):
    spec = {"task": "train", "seconds": 600}
    job_id = backend.submit(spec, _key(backend, spec))

    with pytest.raises(Retryable):  # not yet — later it will be there
        backend.fetch(job_id, "result", "r.txt")

    assert backend.cancel(job_id).state == "cancelled"
    assert backend.cancel(job_id).phase == "done"

    # Never — asking again cannot help, so it must not say it might.
    with pytest.raises(JobError) as never:
        backend.fetch(job_id, "result", "r.txt")
    assert not isinstance(never.value, Retryable)


def test_another_callers_job_does_not_exist_for_you(backend, monkeypatch):
    spec = {"task": "train", "seconds": 600}
    job_id = backend.submit(spec, _key(backend, spec))

    monkeypatch.setenv("DEMO_JOBS_USER", "bob")
    with pytest.raises(NotFound):
        backend.get(job_id)
    assert backend.list(None, 10) == ([], None)


def test_no_user_set_is_a_person_s_job_naming_the_variable(backend, monkeypatch):
    monkeypatch.delenv("DEMO_JOBS_USER")
    with pytest.raises(NeedsAction, match="DEMO_JOBS_USER"):
        backend.caller_id()
