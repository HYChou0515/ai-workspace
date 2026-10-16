"""A workflow step's record as Markdown (plan-workflow-out-of-chat D9).

The executor writes it beside the step's artifact in the journal, so "Ask AI why
it failed" can point a chat at one step as a file. A chat never reads a run's
messages; this file is how the one step it is asked about reaches it.
"""

from __future__ import annotations

import json

from ..resources import Message

#: A tool's output is the bulk of a transcript; past this it is cut, and says so.
TOOL_OUTPUT_MAX_CHARS = 4000


def _fence(text: str) -> str:
    # A fence longer than any run of backticks inside, so the content can't close it.
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def render_transcript(step_id: str, messages: list[Message]) -> str:
    """Every attempt of the step, oldest first. Each prompt opens an attempt."""
    out = [f"# Step `{step_id}`", ""]
    attempt = 0
    for m in messages:
        if m.role == "user":
            attempt += 1
            out += [f"## Attempt {attempt}", "", "### Prompt", "", m.content, ""]
        elif m.role == "assistant":
            if m.reasoning:
                out += ["### Reasoning", "", m.reasoning, ""]
            if m.content:
                out += ["### Reply", "", m.content, ""]
        elif m.role == "tool":
            out += [f"### Tool `{m.tool_name or '?'}`", ""]
            if m.tool_args:
                out += [_fence(json.dumps(m.tool_args, ensure_ascii=False, indent=2)), ""]
            body = m.content
            if len(body) > TOOL_OUTPUT_MAX_CHARS:
                cut = len(body) - TOOL_OUTPUT_MAX_CHARS
                body = f"{body[:TOOL_OUTPUT_MAX_CHARS]}\n… ({cut} more characters cut)"
            out += [_fence(body), ""]
        elif m.role == "error":
            kind = f" ({m.error_kind})" if m.error_kind else ""
            out += [f"### Error{kind}", "", m.content, ""]
    return "\n".join(out).rstrip() + "\n"
