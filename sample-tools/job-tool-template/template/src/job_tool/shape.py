"""Checks the shape tests run on a backend's declarations — without calling the
backend's service (D4)."""

from __future__ import annotations

import functools
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

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


#: Different string-hash seeds for the processes compared. A set of two
#: strings has only two orders, so n seeds all agree by chance with odds of
#: 2^-(n-1): five let a real case through in review (1 in 16); twenty make it
#: about one in half a million, for about a second of CI.
_SEEDS = tuple(str(seed) for seed in range(1, 21))

#: Marks the probe's answer, so a backend that prints when it is imported (a
#: library banner, a timestamp) is not mistaken for part of it.
_MARK = "job_tool.identity="

_PROBE = f"""
import functools, importlib, json, sys
module, _, name = sys.argv[1].partition(":")
backend = functools.reduce(getattr, name.split("."), importlib.import_module(module))
contract = backend.contract
answer = contract.idempotency.identity(contract.example_spec)
print("\\n" + {_MARK!r} + json.dumps(answer, sort_keys=True))  # own line, whatever came before
"""


def _backend(backend_ref: str) -> Any:
    """`module:Class` or `module:Outer.Inner`, resolved as an entry point is."""
    module, _, name = backend_ref.partition(":")
    return functools.reduce(getattr, name.split("."), importlib.import_module(module))


class _NoAnswer(Exception):
    """The probe ended with success and said nothing (a backend that exits at import)."""


#: How much of a failing probe's stderr goes into the problem: the reason, not
#: a wall of it.
_REASON_CHARS = 400

#: Seconds one probe may take — importing the backend and answering once.
PROBE_TIMEOUT = 60.0


def _probe(backend_ref: str, seed: str, path: str) -> str:
    """The identity's answer in a fresh process with this hash seed."""
    done = subprocess.run(
        [sys.executable, "-c", _PROBE, backend_ref],
        env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": path},
        capture_output=True,
        text=True,
        check=True,
        timeout=PROBE_TIMEOUT,
    )
    marked = [line for line in done.stdout.splitlines() if line.startswith(_MARK)]
    if not marked:
        raise _NoAnswer
    return marked[-1].removeprefix(_MARK)


def identity_drift_across_processes(backend_ref: str, extra_path: Sequence[str] = ()) -> list[str]:
    """Whether `identity` answers the same in separate processes, as it must:
    every tool call is a new process, and Python randomises string hashing per
    process — so an identity built on a set's order, `hash()` or `id()` is
    steady within one process (where `identity_problems` looks) and different
    on every call (where it matters). `backend_ref` is the backend's
    "module:Class", as its entry point names it. Empty when the default
    sameness is kept."""
    contract = _backend(backend_ref).contract
    if not contract.idempotency.overrides_identity():
        return []
    path = os.pathsep.join([*extra_path, os.environ.get("PYTHONPATH", "")])
    try:
        answers = {_probe(backend_ref, seed, path) for seed in _SEEDS}
    except subprocess.CalledProcessError as exc:
        reason = " / ".join((exc.stderr or "").strip().splitlines()[-3:])
        if len(reason) > _REASON_CHARS:  # an exception says what is wrong first
            reason = reason[:_REASON_CHARS] + "…"
        return [f"could not run identity() in a separate process: {reason}"]
    except _NoAnswer:
        return [
            "identity() gave no answer in a separate process: importing the backend ended "
            "the process before it could run"
        ]
    except subprocess.TimeoutExpired:
        return [f"identity() did not answer within {PROBE_TIMEOUT:g}s in a separate process"]
    if len(answers) > 1:
        return [
            "identity() gives a different answer in each process (a set's order, hash() or "
            "id() in it?), so every tool call would make the same work a new job: "
            + " | ".join(sorted(answers))
        ]
    return []
