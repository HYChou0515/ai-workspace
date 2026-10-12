"""Checks the shape tests run on a backend's declarations — without calling the
backend's service (D4)."""

from __future__ import annotations

import json

from job_contract import Contract
from jsonschema import Draft202012Validator


def identity_problems(contract: Contract) -> list[str]:
    """What is wrong with an author's `identity` (D49), checked on the contract's
    `example_spec`: it must be a valid spec, and the sameness drawn from it
    must be the same, JSON-serialisable answer twice running — a clock or a
    random number in it would make every resubmit a new job, and nothing else
    would notice. Empty when the default sameness is kept."""
    rule = contract.idempotency
    if not rule.overrides_identity():
        return []
    example = contract.example_spec
    assert example is not None  # the contract refuses an override without one
    problems = [
        f"example_spec does not fit spec_schema: {error.message}"
        for error in Draft202012Validator(contract.spec_schema).iter_errors(example)
    ]
    if problems:
        return problems
    first, second = rule.identity(example), rule.identity(example)
    try:
        written = json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
    except (TypeError, ValueError) as exc:
        return [f"identity() returned something that cannot be written as JSON: {exc}"]
    if written[0] != written[1]:
        return ["identity() gave two different answers for the same spec: " + " vs ".join(written)]
    return []
