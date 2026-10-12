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
    Submitted,
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

    assert rule.key(first, item_id="i1") == rule.key(again, item_id="i1")
    assert rule.key(first, item_id="i1") != rule.key({**first, "command": "eval.py"}, item_id="i1")


class Lenient(Idempotency):
    """An author's sameness: case and surrounding spaces do not make a new job."""

    def identity(self, spec):
        return {"image": spec["image"].lower(), "command": spec["command"].strip()}


def test_an_author_can_say_what_counts_as_the_same_and_the_key_follows_it():
    # D49: the author decides WHAT is compared; the template still hashes it
    # with the item, so the scope cannot be lost by an override.
    rule = Lenient(fields=("image", "command"))
    first = {"image": "Trainer:3", "command": "train.py"}
    again = {"image": "trainer:3", "command": "train.py "}

    assert rule.key(first, item_id="i1") == rule.key(again, item_id="i1")
    assert rule.key(first, item_id="i1") != rule.key(again, item_id="i2")


def test_an_overridden_sameness_must_come_with_an_example_to_check_it_on():
    # The shape test has to run it on something; only the author knows a
    # valid spec, and a missing one would make the check silently absent.
    with pytest.raises(ContractError, match="example_spec"):
        Contract(spec_schema=SPEC, idempotency=Lenient(fields=("image", "command")))

    Contract(
        spec_schema=SPEC,
        idempotency=Lenient(fields=("image", "command")),
        example_spec={"image": "trainer:3", "command": "train.py"},
    )


def test_sameness_lasts_a_day_unless_declared_otherwise():
    # D50: long enough to stop every retry after a lost answer, short enough
    # that the same work can be run again tomorrow without a field to change.
    assert Idempotency(fields=("image",)).window_seconds == 86400
    assert Idempotency(fields=("image",), window_seconds=600).window_seconds == 600


@pytest.mark.parametrize("seconds", [0, -1, True, "1h", float("nan"), float("inf")])
def test_a_window_that_is_not_a_positive_number_of_seconds_is_refused(seconds):
    # NaN compares false with everything: as a window it silently turns
    # sameness off — two identical submits, two jobs.
    with pytest.raises(ContractError, match="window_seconds"):
        Idempotency(fields=("image",), window_seconds=seconds)  # type: ignore[arg-type]


def test_a_submit_says_whether_it_started_a_job():
    # D51: only the backend knows; the shell must not call an old job new.
    assert Submitted(job_id="j1", created=False).created is False


@pytest.mark.parametrize("created", [None, "no", 1])
def test_whether_a_submit_started_a_job_is_yes_or_no(created):
    # None would read as the shell's own "cannot say"; "no" is truthy and
    # would announce a new job. Only a bool says which.
    with pytest.raises(ContractError, match="created"):
        Submitted(job_id="j1", created=created)  # type: ignore[arg-type]


def test_sameness_is_per_item_not_per_person():
    # D41: a job's outputs land in one item's workspace, so the same work sent
    # from two items is two jobs, and from one item is one — whoever sent it.
    spec = {"image": "trainer:3"}
    rule = Idempotency(fields=("image",))

    assert rule.key(spec, item_id="i1") != rule.key(spec, item_id="i2")


def test_without_an_item_sameness_is_the_fields_alone():
    # D47: under the MCP runner there is no item; the key must still be stable,
    # and must not collide with any item's key for the same spec.
    spec = {"image": "trainer:3"}
    rule = Idempotency(fields=("image",))

    assert rule.key(spec, item_id=None) == rule.key(spec, item_id=None)
    assert rule.key(spec, item_id=None) != rule.key(spec, item_id="")
    assert rule.key(spec, item_id=None) != rule.key(spec, item_id="i1")


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
    # With no fields the key depends on the item alone: an item's second,
    # different job would be answered with its first job's id.
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
    # D48: which item it came from, as the backend recorded it; unknown is None.
    assert status.item_id is None
    assert JobStatus(job_id="j1", phase="done", state="ok", item_id="i1").item_id == "i1"


def test_caller_id_is_no_longer_part_of_the_contract():
    # D41 replaced the person with the item: a backend need not say who calls.
    assert "caller_id" not in REQUIRED
    assert not hasattr(JobBackend, "caller_id")


CONTRACT = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)))


class Minimal(JobBackend):
    """Exactly the required calls, and nothing optional."""

    contract = CONTRACT

    def submit(self, spec, key, item_id):
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

        def submit(self, spec, key, item_id): ...
        def find_by_key(self, key): ...
        def list(self, cursor, limit): ...

    with pytest.raises(ContractError, match="get"):
        NoGet.check()
    Minimal.check()


@pytest.mark.parametrize("replacement", [None, property(lambda self: "x")])
def test_a_required_call_replaced_by_something_that_cannot_be_called_is_refused(replacement):
    Broken = type("Broken", (Minimal,), {"get": replacement})

    with pytest.raises(ContractError, match="get"):
        Broken.check()


def test_setting_an_optional_call_to_none_switches_it_off():
    # The natural Python way to remove what a parent class provides.
    class Cancellable(Minimal):
        def cancel(self, job_id):
            return self.get(job_id)

    class NotCancellable(Cancellable):
        cancel = None  # type: ignore[assignment]

    assert "cancel" not in NotCancellable.offers()
    NotCancellable.check()


def test_a_shared_base_without_a_contract_can_be_written():
    # Helpers shared by several backends live on a base that is not a backend
    # itself; only the class that is loaded must be complete.
    class Base(JobBackend):
        def _client(self):
            return "shared helper"

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
