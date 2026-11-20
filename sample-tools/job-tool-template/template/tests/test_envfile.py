"""`env.json` is written from the backend's `env`, never by hand. It lists
what the TOOL reads to reach the backend — never a job's variables (D5)."""

from __future__ import annotations

import json

from backends import Remote

from job_contract import EnvVar
from job_tool.envfile import render, write


def _backend(env):
    return type("Declared", (Remote,), {"env": env})


def test_each_variable_carries_only_what_was_said_about_it():
    rendered = render(
        _backend(
            (
                EnvVar("CLUSTER_TOKEN", "Personal token from the cluster portal", required=True),
                EnvVar("CLUSTER_URL"),
            )
        )
    )

    # An unsaid `required` stays unsaid: it is a third state, not `false`.
    assert json.loads(rendered) == [
        {
            "name": "CLUSTER_TOKEN",
            "description": "Personal token from the cluster portal",
            "required": True,
        },
        {"name": "CLUSTER_URL"},
    ]


def test_saying_nothing_writes_no_file_and_needing_nothing_writes_an_empty_one(tmp_path):
    write(tmp_path, _backend(()))
    assert json.loads((tmp_path / "env.json").read_text()) == []

    # Said nothing: the file goes, so the panel reports "did not say".
    write(tmp_path, _backend(None))
    assert not (tmp_path / "env.json").exists()
