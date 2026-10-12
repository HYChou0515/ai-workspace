"""The check the shape tests run on an author's `identity` (D49): a gate that
exists is only a gate if it fails on what it guards against."""

from __future__ import annotations

import itertools
from pathlib import Path

from backends import SPEC, HashDrift

from job_contract import Contract, Idempotency
from job_tool.shape import identity_drift_across_processes, identity_problems

EXAMPLE = {"image": "trainer:3", "command": "train.py"}


def _contract(identity, example=EXAMPLE):
    rule = type("Rule", (Idempotency,), {"identity": identity})(fields=("image", "command"))
    return Contract(spec_schema=SPEC, idempotency=rule, example_spec=example)


def test_a_steady_sameness_passes():
    assert identity_problems(_contract(lambda self, spec: {"image": spec["image"].lower()})) == []


def test_a_sameness_that_changes_between_two_calls_is_caught():
    tick = itertools.count()
    problems = identity_problems(_contract(lambda self, spec: {"n": next(tick)}))
    assert any("same" in p for p in problems)


def test_a_sameness_that_cannot_be_written_as_json_is_caught():
    problems = identity_problems(_contract(lambda self, spec: {"at": object()}))
    assert any("JSON" in p for p in problems)


def test_an_example_the_spec_schema_rejects_is_caught():
    problems = identity_problems(_contract(lambda self, spec: spec, example={"image": 3}))
    assert any("example_spec" in p for p in problems)


TESTS = str(Path(__file__).resolve().parent)


def test_a_sameness_that_changes_from_one_process_to_the_next_is_caught():
    # Every tool call is a new process: an identity steady within one process
    # but not across them makes every resubmit a new job. Twice in-process
    # cannot see it.
    assert identity_problems(HashDrift.contract) == []  # the in-process check passes
    problems = identity_drift_across_processes("backends:HashDrift", extra_path=[TESTS])
    assert any("process" in p for p in problems)


def test_a_two_element_set_is_caught_too():
    # Two strings have two orders; five fixed seeds put img23/train.py in the
    # same one every time, so the check passed what production would break.
    problems = identity_drift_across_processes("backends:PairDrift", extra_path=[TESTS])
    assert any("process" in p for p in problems)


def test_what_the_backend_prints_on_import_is_not_mistaken_for_its_answer():
    # A timestamped banner differs in every process; the identity does not.
    assert identity_drift_across_processes("probe_backends:Noisy", extra_path=[TESTS]) == []


def test_a_probe_that_cannot_load_the_backend_says_why(monkeypatch):
    import probe_backends  # noqa: F401 - loaded here first, so only the probes are refused

    monkeypatch.setenv("PROBE_BREAK_IMPORT", "1")

    problems = identity_drift_across_processes("probe_backends:Noisy", extra_path=[TESTS])

    assert any("ACME_TOKEN" in p for p in problems)


def test_a_backend_named_by_a_dotted_path_is_found():
    assert identity_drift_across_processes("backends:Holder.Inner", extra_path=[TESTS]) == []


def test_a_steady_sameness_is_the_same_in_every_process():
    # An override, so the processes really are started and compared.
    assert identity_drift_across_processes("backends:SteadyOverride", extra_path=[TESTS]) == []


def test_the_default_sameness_needs_no_example():
    plain = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)))
    assert identity_problems(plain) == []
