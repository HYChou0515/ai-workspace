"""The check the shape tests run on an author's `identity` (D49): a gate that
exists is only a gate if it fails on what it guards against."""

from __future__ import annotations

import itertools

from backends import SPEC

from job_contract import Contract, Idempotency
from job_tool.shape import identity_problems

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


def test_the_default_sameness_needs_no_example():
    plain = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)))
    assert identity_problems(plain) == []
