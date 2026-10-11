"""The three-stage contract, as the platform calls it: argv in, stdout out."""

from __future__ import annotations

import json

from backends import SPEC, Full, Minimal

from job_tool.cli import main


def _run(capsys, argv, backend=Minimal):
    code = main(argv, backend=backend)
    out, err = capsys.readouterr()
    return code, out, err


def test_called_bare_it_lists_the_commands_with_a_description_each(capsys):
    code, out, _ = _run(capsys, [], backend=Full)

    listed = json.loads(out)
    assert code == 0
    assert [c["name"] for c in listed] == [
        "submit", "get", "list", "update", "cancel", "logs", "fetch",
    ]  # fmt: skip
    assert all(c["description"].strip() for c in listed)


def test_called_with_a_command_it_describes_that_command(capsys):
    code, out, _ = _run(capsys, ["submit"])

    described = json.loads(out)
    assert code == 0
    assert described["name"] == "submit"
    assert described["params_json_schema"] == SPEC
    assert described["description"].strip()


def test_a_command_the_backend_does_not_support_is_unknown_and_the_real_ones_are_named(capsys):
    code, out, err = _run(capsys, ["cancel"])

    assert code == 2  # the model can pick a real one and call again
    assert out == ""
    assert "cancel" in err and "submit" in err
