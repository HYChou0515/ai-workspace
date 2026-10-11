"""What the contract refuses, and what it promises, before any backend runs."""

from __future__ import annotations

import pytest

from job_contract import (
    Budget,
    Contract,
    ContractError,
    EnvVar,
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


@pytest.mark.parametrize("seconds", [True, "10", float("nan")])
def test_a_budget_that_is_not_a_number_of_seconds_is_refused(seconds):
    # True would otherwise pass as 1 s, and "10" fail with a bare TypeError.
    with pytest.raises(ContractError, match="get"):
        Budget(get=seconds)  # type: ignore[arg-type]


def test_a_submit_and_its_lookup_must_fit_in_one_call_together():
    # A submit that runs out of time is followed by find_by_key in the SAME
    # call, so the two share the platform's ceiling.
    with pytest.raises(ContractError, match="submit.*find_by_key"):
        Budget(submit=40, find_by_key=20)

    Budget(submit=40, find_by_key=15)


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


def test_sameness_named_on_no_field_at_all_is_refused_when_declared():
    # With no fields the key depends on the caller alone: a person's second,
    # different job would be answered with their first job's id.
    with pytest.raises(ContractError, match="at least one"):
        Contract(spec_schema=SPEC, idempotency=Idempotency(fields=()))


def test_a_variable_declared_twice_is_refused():
    # Two descriptions of one variable: whichever the panel shows, the other lied.
    with pytest.raises(ContractError, match="GPU_API_TOKEN"):
        Contract(
            spec_schema=SPEC,
            idempotency=Idempotency(fields=("image",)),
            env=(EnvVar("GPU_API_TOKEN"), EnvVar("GPU_API_TOKEN", required=True)),
        )


def test_not_saying_which_variables_is_not_saying_none_are_needed():
    said_nothing = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)))
    needs_none = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)), env=())

    assert said_nothing.env is None
    assert needs_none.env == ()


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


def test_a_backend_that_declares_no_contract_is_refused_by_the_check():
    class Undeclared(Minimal):
        contract = None  # type: ignore[assignment]

    with pytest.raises(ContractError, match="Undeclared"):
        Undeclared.check()


def test_a_backend_missing_a_required_call_is_refused_by_the_check():
    # Not at instantiation: stages 1 and 2 of the CLI, the shape tests and the
    # builder's smoke never instantiate, so a missing `get` would ship and fail
    # on every call. `check` is what loading a backend runs.
    class NoGet(JobBackend):
        contract = CONTRACT

        def caller_id(self): ...
        def submit(self, spec, key): ...
        def find_by_key(self, key): ...
        def list(self, cursor, limit): ...

    with pytest.raises(ContractError, match="get"):
        NoGet.check()
    Minimal.check()


def test_a_shared_base_without_a_contract_can_be_written():
    # Helpers shared by several backends live on a base that is not a backend
    # itself; only the class that is loaded must be complete.
    class Base(JobBackend):
        def caller_id(self):
            return "ann"

    class Complete(Base, Minimal):
        pass

    Complete.check()
    with pytest.raises(ContractError, match="contract"):
        Base.check()


def test_a_backend_offers_the_required_calls_and_only_the_optional_ones_it_wrote():
    class Cancellable(Minimal):
        def cancel(self, job_id):
            return self.get(job_id)

    assert Minimal.offers() == REQUIRED
    assert Cancellable.offers() == (*REQUIRED, "cancel")


def test_update_and_its_patch_schema_come_together_or_not_at_all():
    # An update with no schema cannot tell the model what a patch may say; a
    # schema with no update describes a call that does not exist.
    class UpdateWithoutSchema(Minimal):
        def update(self, job_id, patch):
            return self.get(job_id)

    class SchemaWithoutUpdate(Minimal):
        contract = Contract(
            spec_schema=SPEC,
            idempotency=Idempotency(fields=("image",)),
            patch_schema={"type": "object"},
        )

    with pytest.raises(ContractError, match="patch_schema"):
        UpdateWithoutSchema.check()
    with pytest.raises(ContractError, match="update"):
        SchemaWithoutUpdate.check()
