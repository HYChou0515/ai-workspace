"""Run one scenario through a model and record what it did.

The model arrives as a ``chat`` callable, so everything here is exercisable with
a scripted double and no LLM. The litellm-backed implementation lives in the CLI.

The prompt is assembled by the app's OWN functions (``apps.catalog._compose_prompt``
and the three readers beside it), so the guidance under test is the guidance a
real turn would receive, and cannot drift from it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import msgspec

from ..apps import skills as skills_mod
from ..apps.catalog import (
    _compose_prompt,
    _read_app_text,
    _read_base_preamble,
    _read_sandbox_preamble,
)
from ..apps.manifest import load_app_manifest
from .scenario import Scenario
from .tools import Event, schemas
from .tools import run as run_tool

#: Mirrors the header ``apps.skills.build_applied_skills_block`` renders for the
#: Apply chip. ``test_applied_header_matches_production`` fails if that drifts.
APPLIED_HEADER = (
    "## Apply these skills now\n\n"
    "The user selected the following skill(s) to apply THIS turn. Read them and "
    "follow them as you answer."
)

#: One tool call per response — apps/_base.md:9. Extra calls in one reply are
#: answered with a nudge rather than executed, which is what the app's small-model
#: guidance asks for.
_EXTRA_CALL_NOTE = "Only one tool call per response is executed. Resend this one alone."


class ToolCall(msgspec.Struct, frozen=True):
    id: str
    name: str
    args: dict


class Turn(msgspec.Struct, frozen=True):
    """One assistant reply: free text, tool calls, or both."""

    content: str = ""
    tool_calls: list[ToolCall] = msgspec.field(default_factory=list)


Chat = Callable[[list[dict], list[dict]], Turn]


class Transcript(msgspec.Struct, frozen=True):
    calls: list[str]
    events: list[Event]
    answer: str
    steps: int
    #: "answered" | "step-limit"
    ended: str


def system_prompt(app_slug: str = "rca") -> str:
    """What a turn of ``app_slug`` really starts with, built by the app's own
    composer so this cannot drift from production."""
    manifest = load_app_manifest(app_slug)
    return _compose_prompt(
        _read_app_text(app_slug, manifest.agent.prompt_file),
        "",
        [],
        preamble=_read_base_preamble(),
        sandbox_preamble=_read_sandbox_preamble(),
    )


def applied_block(skill_name: str, skill_md: str) -> str:
    """The skill body wrapped exactly as the Apply chip wraps it."""
    _front, body = skills_mod._parse_frontmatter(skill_md.encode())
    return f"{APPLIED_HEADER}\n\n### {skill_name}\n\n{body}"


def run_scenario(
    chat: Chat,
    scenario: Scenario,
    work: Path,
    *,
    skill_name: str = "",
    skill_md: str = "",
    app_slug: str = "rca",
    max_steps: int = 20,
) -> Transcript:
    """Drive one scenario to an answer or the step limit.

    ``skill_md`` empty is the control arm — the same question with no guidance,
    which is the only thing that makes a green run mean anything.
    """
    first = scenario.prompt
    if skill_md:
        first = f"{applied_block(skill_name, skill_md)}\n\n{scenario.prompt}"
    messages: list[dict] = [
        {"role": "system", "content": system_prompt(app_slug)},
        {"role": "user", "content": first},
    ]
    tools, calls, events = schemas(), [], []
    for step in range(max_steps):
        turn = chat(messages, tools)
        messages.append(
            {
                "role": "assistant",
                "content": turn.content,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": msgspec.json.encode(c.args).decode(),
                        },
                    }
                    for c in turn.tool_calls
                ],
            }
        )
        if not turn.tool_calls:
            return Transcript(calls, events, turn.content, step + 1, "answered")
        head, *rest = turn.tool_calls
        calls.append(head.name)
        try:
            output = run_tool(head.name, head.args, work, events)
        except (OSError, KeyError, ValueError) as e:
            # The model must see its own mistakes; swallowing them would score
            # the harness instead of the guidance.
            output = f"tool raised: {type(e).__name__}: {e}"
        messages.append(
            {"role": "tool", "tool_call_id": head.id, "name": head.name, "content": output}
        )
        for extra in rest:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": extra.id,
                    "name": extra.name,
                    "content": _EXTRA_CALL_NOTE,
                }
            )
    return Transcript(calls, events, "", max_steps, "step-limit")
