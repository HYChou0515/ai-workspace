"""Which commands the model is offered, and what each one accepts."""

from __future__ import annotations

from backends import PATCH, SPEC, Full, Minimal

from job_tool.commands import commands


def test_a_backend_with_only_the_required_calls_is_offered_submit_get_and_list():
    # caller_id and find_by_key are the shell's own business; offering them
    # would only give the model two ways to get confused.
    assert list(commands(Minimal)) == ["submit", "get", "list"]


def test_every_optional_call_the_backend_wrote_becomes_a_command():
    assert list(commands(Full)) == [
        "submit", "get", "list", "update", "cancel", "logs", "fetch",
    ]  # fmt: skip


def test_submit_accepts_the_authors_spec_and_update_the_authors_patch():
    # The model fills the author's own fields — no wrapper object to get wrong.
    cmds = commands(Full)
    assert cmds["submit"].params_json_schema == SPEC

    update = cmds["update"].params_json_schema
    assert update["properties"]["patch"] == PATCH
    assert update["required"] == ["job_id", "patch"]
    assert cmds["get"].params_json_schema["required"] == ["job_id"]
