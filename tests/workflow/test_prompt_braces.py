"""A prompt has to be able to contain a literal brace (#428 §1).

An `outputs` node asks the model to reply with a JSON object. The one thing an author
needs to write in that node's prompt is what the object looks like — and every `{...}` in
a prompt is an interpolation reference, so `reply with {"count": 3}` failed static
validation with `references unknown variable '"count": 3'`. There was no escape: `{{}}`,
a backslash and added spaces all failed the same way. So the channel demanded a shape the
author was not allowed to describe.

`{{` and `}}` are the literal braces, the same escape `str.format` uses.
"""

import json
from typing import Any

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import _resolve, parse_def, validate_def
from workspace_app.workflow.handle import WorkflowHandle


def _def(prompt: str) -> Any:
    return parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "agent",
                        "name": "s",
                        "phase": "p",
                        "outputs": {"count": "int"},
                        "prompt": prompt,
                    }
                ],
            }
        )
    )


def _wf(**config: Any) -> WorkflowHandle:
    return WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", config=config)


def test_an_escaped_brace_is_not_a_reference():
    assert validate_def(_def('reply with {{"count": 3}}')) == []


def test_a_bare_brace_is_still_a_reference_and_still_caught():
    """The escape must not switch typo-catching off — an unescaped unknown root still errs."""
    errs = validate_def(_def("reply with {nope.field}"))
    assert errs and "unknown variable 'nope'" in errs[0]


def test_a_real_reference_next_to_an_escaped_brace_still_resolves():
    assert validate_def(_def('use {config.collections} and reply {{"count": 3}}')) == []


async def test_an_escaped_brace_renders_as_a_literal_brace():
    """What the model actually receives: the braces, not the escape."""
    out = await _resolve('reply {{"count": {config.n}}}', {"config": {"n": 7}}, _wf(n=7))
    assert out == 'reply {"count": 7}'


async def test_a_whole_template_of_escaped_braces_is_not_a_lookup():
    assert await _resolve('{{"count": 3}}', {}, _wf()) == '{"count": 3}'
