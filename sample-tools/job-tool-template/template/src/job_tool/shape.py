"""Checks the shape tests run on a backend's declarations — without calling the
backend's service (D4)."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence

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


#: Different string-hash seeds for the processes compared. Five: two could
#: agree by chance on how a small set is ordered; five practically cannot.
_SEEDS = ("1", "2", "3", "4", "5")

_PROBE = """
import importlib, json, sys
module, _, name = sys.argv[1].partition(":")
contract = getattr(importlib.import_module(module), name).contract
print(json.dumps(contract.idempotency.identity(contract.example_spec), sort_keys=True))
"""


def identity_drift_across_processes(backend_ref: str, extra_path: Sequence[str] = ()) -> list[str]:
    """Whether `identity` answers the same in separate processes, as it must:
    every tool call is a new process, and Python randomises string hashing per
    process — so an identity built on a set's order, `hash()` or `id()` is
    steady within one process (where `identity_problems` looks) and different
    on every call (where it matters). `backend_ref` is the backend's
    "module:Class", as its entry point names it. Empty when the default
    sameness is kept."""
    module, _, name = backend_ref.partition(":")
    contract = getattr(importlib.import_module(module), name).contract
    if not contract.idempotency.overrides_identity():
        return []
    path = os.pathsep.join([*extra_path, os.environ.get("PYTHONPATH", "")])
    answers = {
        subprocess.run(
            [sys.executable, "-c", _PROBE, backend_ref],
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": path},
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        for seed in _SEEDS
    }
    if len(answers) > 1:
        return [
            "identity() gives a different answer in each process (a set's order, hash() or "
            "id() in it?), so every tool call would make the same work a new job: "
            + " | ".join(sorted(answers))
        ]
    return []
