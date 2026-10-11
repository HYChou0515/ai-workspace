"""`env.json` is written from the contract, never by hand (D19)."""

from __future__ import annotations

import json

from backends import SPEC, Minimal

from job_contract import Contract, EnvVar, Idempotency
from job_tool.envfile import render, write


def _backend(env):
    class Declared(Minimal):
        contract = Contract(spec_schema=SPEC, idempotency=Idempotency(fields=("image",)), env=env)

    return Declared


def test_each_variable_carries_only_what_was_said_about_it():
    rendered = render(
        _backend(
            (
                EnvVar("GPU_API_TOKEN", "Personal token from the cluster portal", required=True),
                EnvVar("GPU_API_BASE"),
            )
        ).contract
    )

    # An unsaid `required` stays unsaid: it is a third state, not `false`.
    assert json.loads(rendered) == [
        {
            "name": "GPU_API_TOKEN",
            "description": "Personal token from the cluster portal",
            "required": True,
        },
        {"name": "GPU_API_BASE"},
    ]


def test_saying_nothing_writes_no_file_and_needing_nothing_writes_an_empty_one(tmp_path):
    write(tmp_path, _backend(()).contract)
    assert json.loads((tmp_path / "env.json").read_text()) == []

    # Said nothing: the file goes, so the panel reports "did not say".
    write(tmp_path, _backend(None).contract)
    assert not (tmp_path / "env.json").exists()
