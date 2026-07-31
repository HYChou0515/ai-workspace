"""Your tests. They run with plain `pytest` — no platform, no container.

The three below are the ones worth having whatever your tool does: the
contract holds, the arguments are validated, and the work is right.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from my_tool.commands import COMMANDS
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
