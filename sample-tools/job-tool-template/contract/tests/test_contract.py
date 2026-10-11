"""What the contract refuses, and what it promises, before any backend runs."""

from __future__ import annotations

import pytest

from job_contract import (
    Budget,
    Contract,
    ContractError,
    Idempotency,
    REQUIRED,
    JobBackend,
    JobStatus,
)


def test_a_budget_past_the_platform_ceiling_is_refused_when_declared():
    # The platform stops a call at 60 s; a budget beyond 55 leaves the shell no
    # time to tell the model what happened, so it is refused at declaration,
    # not discovered at run time.
    with pytest.raises(ContractError, match="get"):
        Budget(get=56)


def test_two_submits_of_the_same_job_get_the_same_key_whatever_else_differs():
    rule = Idempotency(fields=("image", "command"))
    first = {"image": "trainer:3", "command": "train.py", "note": "first try"}
    again = {"note": "second try", "command": "train.py", "image": "trainer:3"}

    assert rule.key(first, caller="ann") == rule.key(again, caller="ann")
    assert rule.key(first, caller="ann") != rule.key({**first, "command": "eval.py"}, caller="ann")


def test_the_same_spec_from_two_callers_is_two_jobs_unless_declared_shared():
    spec = {"image": "trainer:3"}

    mine = Idempotency(fields=("image",))
    assert mine.key(spec, caller="ann") != mine.key(spec, caller="bob")

    shared = Idempotency(fields=("image",), per_user=False)
    assert shared.key(spec, caller="ann") == shared.key(spec, caller="bob")


SPEC = {
    "type": "object",
    "properties": {"image": {"type": "string"}, "command": {"type": "string"}},
    "required": ["image", "command"],
}


def test_sameness_named_on_a_field_the_spec_does_not_have_is_refused_when_declared():
    # A typo here would make every submit a different job — silently, at the
    # first double-submit. Refusing the declaration is the only place it shows.
    with pytest.raises(ContractError, match="imgae"):
        Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("imgae",)))

    Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)))


def test_a_status_outside_the_three_phases_is_refused():
    # The shell decides what the model hears from `phase` alone. A backend's
    # own word ("finished", "SUSPENDED") belongs in `state`; in `phase` it
    # would be read as nothing at all.
    with pytest.raises(ContractError, match="finished"):
        JobStatus(job_id="j1", phase="finished", state="finished")  # type: ignore[arg-type]

    status = JobStatus(job_id="j1", phase="running", state="suspended")
    assert status.detail == {}


CONTRACT = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)))


class Minimal(JobBackend):
    """Exactly the required calls, and nothing optional."""

    contract = CONTRACT

    def caller_id(self):
        return "ann"

    def submit(self, spec, key):
        return "j1"

    def find_by_key(self, key):
        return None

    def get(self, job_id):
        return JobStatus(job_id=job_id, phase="running", state="queued")

    def list(self, cursor, limit):
        return [], None


def test_a_backend_that_declares_no_contract_is_refused_when_written():
    with pytest.raises(ContractError, match="Undeclared"):

        class Undeclared(Minimal):
            contract = None  # type: ignore[assignment]


def test_a_backend_missing_a_required_call_cannot_be_built():
    class NoCaller(JobBackend):
        contract = CONTRACT

        def submit(self, spec, key): ...
        def find_by_key(self, key): ...
        def get(self, job_id): ...
        def list(self, cursor, limit): ...

    with pytest.raises(TypeError, match="caller_id"):
        NoCaller()  # type: ignore[abstract]


def test_a_backend_offers_the_required_calls_and_only_the_optional_ones_it_wrote():
    class Cancellable(Minimal):
        def cancel(self, job_id):
            return self.get(job_id)

    assert Minimal.offers() == REQUIRED
    assert Cancellable.offers() == (*REQUIRED, "cancel")
