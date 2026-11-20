"""What the contract refuses, and what it promises, before any backend runs."""

from __future__ import annotations

import pytest

from job_contract import (
    COMMANDS,
    OPTIONAL,
    REQUIRED,
    Budget,
    Capacity,
    ContractError,
    Entry,
    EnvVar,
    JobBackend,
    NeedsAction,
    NotFound,
    QueuedJob,
    Retryable,
    Status,
    Worker,
)

RESOURCES = {
    "type": "object",
    "properties": {"gpus": {"type": "integer", "minimum": 0}},
    "required": ["gpus"],
}


class Whole(JobBackend):
    """Every required call, and the two declarations every backend makes."""

    run_description = "Runs the folder on the cluster: put the command in run.sh."
    resources_schema = RESOURCES

    def upload(self, job_id, path, offset, data):
        return None

    def start(self, job_id, resources):
        return None

    def status(self, job_id):
        return Status("queued")

    def list_files(self, job_id, path):
        return []

    def download(self, job_id, path, offset, limit):
        return b""

    def logs(self, job_id, offset, limit):
        return "", 0

    def cancel(self, job_id):
        return None


class WithCapacity(Whole):
    def capacity(self):
        return Capacity()


# ── the calls ────────────────────────────────────────────────────────────────


def test_the_required_calls_are_the_seven_the_shell_needs_and_capacity_is_optional():
    assert REQUIRED == (
        "upload", "start", "status", "list_files", "download", "logs", "cancel",
    )  # fmt: skip
    assert OPTIONAL == ("capacity",)


def test_a_whole_backend_passes_and_offers_its_required_calls():
    Whole.check()
    assert Whole.offers() == REQUIRED


def test_capacity_is_offered_only_by_a_backend_that_writes_it():
    WithCapacity.check()
    assert WithCapacity.offers() == (*REQUIRED, "capacity")


@pytest.mark.parametrize("missing", REQUIRED)
def test_a_backend_missing_a_required_call_is_refused_when_loaded(missing):
    # Defining it is allowed — a base class sharing helpers may be partial —
    # but the class that is loaded must be whole, or a command fails on every call.
    lacking = type("Lacking", (Whole,), {missing: None})

    with pytest.raises(ContractError, match=missing):
        lacking.check()


def test_an_abstract_required_call_is_refused_too():
    class Abstract(JobBackend):
        run_description = Whole.run_description
        resources_schema = RESOURCES

    with pytest.raises(ContractError, match="upload"):
        Abstract.check()


def test_setting_an_inherited_capacity_to_none_switches_queue_off():
    off = type("Off", (WithCapacity,), {"capacity": None})

    off.check()
    assert "capacity" not in off.offers()


# ── what a backend declares ──────────────────────────────────────────────────


@pytest.mark.parametrize("text", [None, "", "   ", 42])
def test_a_backend_must_describe_how_to_prepare_a_folder_for_it(text):
    # The model writes the folder from this description alone (D4): which file
    # is run, which files set up the environment, what the machine has.
    bad = type("Bad", (Whole,), {"run_description": text})

    with pytest.raises(ContractError, match="run_description"):
        bad.check()


def test_a_backend_without_a_run_description_at_all_is_refused():
    class Silent(JobBackend):
        resources_schema = RESOURCES
        upload = Whole.upload
        start = Whole.start
        status = Whole.status
        list_files = Whole.list_files
        download = Whole.download
        logs = Whole.logs
        cancel = Whole.cancel

    with pytest.raises(ContractError, match="run_description"):
        Silent.check()


@pytest.mark.parametrize(
    "schema",
    [None, [], {"type": "array"}, {"properties": {}}],
)
def test_the_resources_schema_must_describe_an_object(schema):
    # `run` takes the resources as one object; a schema of anything else would
    # refuse every call made with it.
    bad = type("Bad", (Whole,), {"resources_schema": schema})

    with pytest.raises(ContractError, match="resources_schema"):
        bad.check()


def test_a_description_may_override_any_command_but_run():
    ok = type("Ok", (WithCapacity,), {"descriptions": {"logs": "The cluster's log.", "queue": "x"}})
    ok.check()

    # run's description is run_description: two places for one text drift.
    with pytest.raises(ContractError, match="run_description"):
        type("Bad", (Whole,), {"descriptions": {"run": "x"}}).check()


def test_a_description_for_a_command_that_does_not_exist_is_refused():
    with pytest.raises(ContractError, match="lsit"):
        type("Bad", (Whole,), {"descriptions": {"lsit": "x"}}).check()

    # queue exists only with capacity: describing it without would say nothing.
    with pytest.raises(ContractError, match="queue"):
        type("Bad", (Whole,), {"descriptions": {"queue": "x"}}).check()


def test_an_empty_description_override_is_refused():
    with pytest.raises(ContractError, match="logs"):
        type("Bad", (Whole,), {"descriptions": {"logs": "  "}}).check()


def test_the_commands_are_fixed_by_the_contract():
    assert COMMANDS == ("run", "jobs", "queue", "files", "fetch", "logs", "cancel")


def test_env_declared_twice_is_refused():
    bad = type("Bad", (Whole,), {"env": (EnvVar("TOKEN"), EnvVar("TOKEN"))})

    with pytest.raises(ContractError, match="TOKEN"):
        bad.check()


def test_env_says_nothing_by_default_which_is_not_needs_none():
    assert Whole.env is None


def test_a_budget_that_is_not_a_budget_is_refused():
    bad = type("Bad", (Whole,), {"budget": {"upload": 5}})

    with pytest.raises(ContractError, match="budget"):
        bad.check()


# ── budgets ──────────────────────────────────────────────────────────────────


def test_a_budget_past_the_platform_ceiling_is_refused_when_declared():
    # The platform stops a call at 60 s; a budget beyond 55 leaves the shell no
    # time to tell the model what happened.
    with pytest.raises(ContractError, match="status"):
        Budget(status=56)


@pytest.mark.parametrize("seconds", [True, "10", float("nan"), 0, -1])
def test_a_budget_that_is_not_a_positive_number_of_seconds_is_refused(seconds):
    with pytest.raises(ContractError, match="upload"):
        Budget(upload=seconds)  # type: ignore[arg-type]


def test_a_start_and_the_status_that_checks_it_must_fit_in_one_call_together():
    # A start that runs out of time is followed by status in the SAME call, to
    # learn whether it landed — so the two share the platform's ceiling.
    with pytest.raises(ContractError, match="start.*status"):
        Budget(start=45, status=15)

    Budget(start=45, status=10)


# ── what the calls answer ────────────────────────────────────────────────────


@pytest.mark.parametrize("phase", ["queued", "running", "succeeded", "failed", "unknown"])
def test_a_status_has_one_of_five_phases(phase):
    assert Status(phase).phase == phase


def test_a_phase_outside_the_five_is_refused_and_the_backends_word_belongs_in_state():
    with pytest.raises(ContractError, match="state"):
        Status("cancelled")  # type: ignore[arg-type]

    cancelled = Status("failed", state="cancelled by ann")
    assert cancelled.finished


def test_only_succeeded_and_failed_are_finished():
    phases = ("queued", "running", "succeeded", "failed", "unknown")
    assert [p for p in phases if Status(p).finished] == ["succeeded", "failed"]  # type: ignore[arg-type]


def test_a_negative_queue_position_is_refused():
    with pytest.raises(ContractError, match="position"):
        Status("queued", position=-1)


@pytest.mark.parametrize("path", ["", "/etc/passwd", "a/../../b", "../x"])
def test_an_entry_must_name_a_path_inside_the_job(path):
    with pytest.raises(ContractError, match="path"):
        Entry(path, is_dir=False, size=1)


def test_an_entry_with_a_negative_size_is_refused():
    with pytest.raises(ContractError, match="size"):
        Entry("out/model.pt", is_dir=False, size=-1)


def test_capacity_holds_workers_a_queue_and_an_optional_quota():
    capacity = Capacity(
        workers=(Worker("gpu-1", "busy", job_id="j1", free={"gpus": 0}),),
        queue=(QueuedJob("j2", resources={"gpus": 8}, waiting_since="2026-11-20T09:00"),),
        quota={"gpu_hours": "12 of 20"},
    )
    assert capacity.workers[0].job_id == "j1"
    assert Capacity().quota is None


def test_the_errors_carry_the_exit_code_the_model_acts_on():
    assert (Retryable.exit_code, NeedsAction.exit_code, NotFound.exit_code) == (2, 3, 1)
