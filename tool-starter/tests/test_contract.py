"""Your tests. They run with plain `pytest` — no platform, no container.

The three below are the ones worth having whatever your tool does: the
contract holds, the arguments are validated, and the work is right.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from my_tool.commands import COMMANDS
from my_tool.common import NeedsAction, Retryable, ToolError
from my_tool.commands.count import Args, run


def test_every_command_describes_itself():
    # What the model is shown. A command with an empty description is a
    # command the model will not use.
    for name, cmd in COMMANDS.items():
        assert cmd.DESCRIPTION.strip(), f"{name} has no description"
        assert cmd.Args.model_json_schema()["type"] == "object"


def test_arguments_are_validated_not_trusted():
    schema = Args.model_json_schema()
    assert "path" in schema["required"]
    assert schema["properties"]["path"]["description"]


def test_it_counts(tmp_path: Path, monkeypatch):
    # cwd IS the workspace when the platform runs you, so a test that chdirs
    # is testing the real thing.
    (tmp_path / "notes.txt").write_text("one two\n\nthree\n")
    monkeypatch.chdir(tmp_path)

    answer = json.loads(run(Args(path="notes.txt", ignore_blank_lines=True)))

    assert answer == {"path": "notes.txt", "lines": 2, "words": 3}


def test_a_decorated_command_is_indistinguishable_from_a_spelled_out_one():
    """`cli.py` asks every command for the same three things. That is what
    makes the choice between the two styles a matter of taste rather than a
    fork in the contract."""
    spelled_out, decorated = COMMANDS["count"], COMMANDS["head"]

    for cmd in (spelled_out, decorated):
        assert cmd.DESCRIPTION.strip()
        assert issubclass(cmd.Args, BaseModel)
        assert callable(cmd.run)


def test_the_decorator_takes_its_schema_from_the_annotation():
    # One source of truth: the model the code validates with is the model the
    # schema is generated from.
    from my_tool.commands.head import Args as HeadArgs

    assert COMMANDS["head"].Args is HeadArgs
    assert COMMANDS["head"].Args.model_json_schema()["properties"]["lines"]["maximum"] == 200


def test_a_failure_the_model_can_fix_exits_retryable(tmp_path, monkeypatch):
    """The exit code is how the platform decides what to tell the model next,
    so a wrong path — which the model chose and can change — has to say
    'try again' rather than 'this failed'."""
    import subprocess
    import sys

    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "my_tool.cli", "count", '{"path":"absent.txt"}'],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == Retryable.exit_code
    assert "absent.txt" in proc.stderr  # the detail lives in the message
    assert proc.stdout == ""  # stdout stays the answer channel


def test_arguments_the_model_got_wrong_exit_retryable(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "my_tool.cli", "count", '{"nope":1}'],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == Retryable.exit_code


def test_the_codes_are_the_published_ones():
    # The platform reads these numbers. They are a published contract, not an
    # internal detail to renumber.
    assert ToolError.exit_code == 1
    assert Retryable.exit_code == 2
    assert NeedsAction.exit_code == 3


def test_a_file_the_tool_may_not_read_needs_a_person(tmp_path: Path, monkeypatch):
    """A path that exists but cannot be opened is not the model's mistake, and
    calling again changes nothing — someone has to fix the permission. That is
    exactly what `NeedsAction` is for.

    Found for real: the MCP runner drops to the uid that owns the workspace,
    so a file belonging to somebody else reached `read_text` and came back as
    a traceback. A traceback tells the model nothing it can act on and tells
    the user nothing they can fix."""
    import pytest

    locked = tmp_path / "locked.txt"
    locked.write_text("secret\n")
    locked.chmod(0o000)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(NeedsAction) as caught:
        run(Args(path="locked.txt"))

    assert "locked.txt" in str(caught.value)
    assert caught.value.exit_code == 3


def test_both_authoring_styles_report_a_missing_file_the_same_way(tmp_path: Path, monkeypatch):
    """`count` and `head` are the same contract written two ways, so they must
    fail the same way too. `head` used to return `{"error": …}` and exit 0 —
    a call the platform reads as successful, carrying a payload that says it
    was not. The model is then told the tool worked."""
    import pytest

    from my_tool.commands.head import Args as HeadArgs
    from my_tool.commands.head import head

    monkeypatch.chdir(tmp_path)

    for call in (lambda: run(Args(path="nope.txt")), lambda: head(HeadArgs(path="nope.txt"))):
        with pytest.raises(ToolError) as caught:
            call()
        assert caught.value.exit_code == Retryable.exit_code
        assert "nope.txt" in str(caught.value)


def test_both_authoring_styles_send_a_permission_problem_to_a_person(tmp_path: Path, monkeypatch):
    import pytest

    from my_tool.commands.head import Args as HeadArgs
    from my_tool.commands.head import head

    locked = tmp_path / "locked.txt"
    locked.write_text("secret\n")
    locked.chmod(0o000)
    monkeypatch.chdir(tmp_path)

    for call in (lambda: run(Args(path="locked.txt")), lambda: head(HeadArgs(path="locked.txt"))):
        with pytest.raises(NeedsAction) as caught:
            call()
        assert caught.value.exit_code == 3
