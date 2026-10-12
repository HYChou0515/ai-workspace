"""The shape checks: the backend actually installed here, as the platform will
call it. CI runs these before `build-tool`, so a backend that declares what
cannot hold, or leaves out a required call, never becomes an artifact (D3).
They never call the backend's own work — only its declarations and the
commands built from them (D4) — so what a call DOES, such as returning the same
job for the same key, is the backend author's to keep.

Remove the CI step if you must; you own this repository. Do it on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from job_tool.cli import main
from job_tool.discover import load_backend
from job_tool.envfile import FILENAME, render
from job_tool.shape import identity_problems

ROOT = Path(__file__).resolve().parents[1]


def test_exactly_one_backend_is_installed_and_its_contract_holds():
    # Loading it is the check: a contract value that cannot hold raises on
    # import, and a missing required call or a lone update/patch_schema is
    # refused by JobBackend.check, which load_backend runs.
    load_backend()


def test_the_tool_lists_its_commands_and_describes_each_one(capsys):
    assert main([]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed, "the tool offers no commands"

    for entry in listed:
        assert entry["description"].strip(), f"{entry['name']} has no description"
        assert main([entry["name"]]) == 0
        described = json.loads(capsys.readouterr().out)
        # A schema the validator rejects would reject every call made with it.
        Draft202012Validator.check_schema(described["params_json_schema"])


def test_what_counts_as_the_same_job_is_steady():
    # D49: an overridden identity() that drifts would make every resubmit a new
    # job; checked on the contract's example_spec, never on the service.
    assert identity_problems(load_backend().contract) == []


def test_env_json_says_what_the_contract_says():
    # Regenerate with `uv run python -m job_tool.envfile` when this fails.
    expected = render(load_backend().contract)
    on_disk = ROOT / FILENAME
    actual = on_disk.read_text(encoding="utf-8") if on_disk.exists() else None
    assert actual == expected, "env.json is stale: run `uv run python -m job_tool.envfile`"
