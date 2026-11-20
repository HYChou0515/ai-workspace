"""The three-stage contract, as the platform calls it: argv in, stdout out."""

from __future__ import annotations

import json

import pytest
from backends import RESOURCES, Machine, Remote, RemoteWithQueue

from job_tool.cli import main


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Remote.machine = Machine()


def _run(capsys, argv, backend=Remote):
    code = main(argv, backend=backend)
    out, err = capsys.readouterr()
    return code, out, err


def test_called_bare_it_lists_the_commands_with_a_description_each(capsys):
    code, out, _ = _run(capsys, [], backend=RemoteWithQueue)

    listed = json.loads(out)
    assert code == 0
    assert [c["name"] for c in listed] == [
        "run", "jobs", "queue", "files", "fetch", "logs", "cancel",
    ]  # fmt: skip
    assert all(c["description"].strip() for c in listed)


def test_without_capacity_there_is_no_queue_command(capsys):
    _, out, _ = _run(capsys, [])

    assert "queue" not in [c["name"] for c in json.loads(out)]


def test_run_takes_a_folder_and_the_backends_resources(capsys):
    code, out, _ = _run(capsys, ["run"])

    described = json.loads(out)
    assert code == 0
    assert described["params_json_schema"]["properties"]["resources"] == RESOURCES
    assert described["params_json_schema"]["required"] == ["workdir", "resources"]
    assert described["description"].startswith(Remote.run_description)


def test_a_command_the_backend_does_not_support_is_unknown_and_the_real_ones_are_named(capsys):
    code, out, err = _run(capsys, ["queue"])

    assert code == 2  # the model can pick a real one and call again
    assert out == ""
    assert "queue" in err and "jobs" in err


def test_resources_outside_the_backends_schema_never_reach_it(capsys):
    code, out, err = _run(capsys, ["run", json.dumps({"workdir": "x", "resources": {"gpus": 99}})])

    assert code == 2
    assert out == ""
    assert "resources/gpus" in err
    assert Remote.machine.calls == []


def test_arguments_the_command_does_not_take_are_refused(capsys):
    code, _, err = _run(capsys, ["jobs", json.dumps({"verbose": True})])

    assert code == 2
    assert "verbose" in err


def test_arguments_that_are_not_json_are_refused(capsys):
    code, _, err = _run(capsys, ["jobs", "{oops"])

    assert code == 2
    assert "not JSON" in err


def test_more_arguments_than_one_json_object_are_refused(capsys):
    code, _, err = _run(capsys, ["logs", '{"job_id": "x"}', '{"job_id": "y"}'])

    assert code == 2
    assert "one" in err
    assert Remote.machine.calls == []
