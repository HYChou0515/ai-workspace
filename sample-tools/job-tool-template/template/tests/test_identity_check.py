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


def test_a_steady_sameness_is_the_same_in_every_process():
    # An override, so the processes really are started and compared.
    assert identity_drift_across_processes("backends:SteadyOverride", extra_path=[TESTS]) == []


def test_the_default_sameness_needs_no_example():
    plain = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)))
    assert identity_problems(plain) == []
