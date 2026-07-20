"""One ceiling every tool's model-visible output has to pass under.

The per-tool caps (`read_file`'s line/char budget, `exec`'s
`exec_output_max_chars`) are opt-ins: a tool that forgets to apply one
returns whatever it likes straight into the message history. That is how
`list_files` came to answer a big workspace with one line per file and
blow the model's context on the FIRST tool call of a turn — and the base
prompt tells every agent to start by listing the workspace, so it was on
the happy path, not an edge case.

So the ceiling moves from "each author remembers" to "the toolset
enforces": every ``FunctionTool`` handed out by ``build_tools`` (and by
the tool-package registry) carries a **tool output guardrail** — the
SDK's own post-invoke seam, whose ``reject_content`` REPLACES the value
the model sees (``agents/run_internal/tool_execution.py``
``_execute_tool_output_guardrails``). No new wrapper layer of ours, and
it composes with the args-recovery wrap already around
``on_invoke_tool``.

This is a BACKSTOP, not a substitute for a tool knowing its own shape.
All it can do is cut; a tool that *pages* (``read_file``'s
offset/limit, ``list_files``'s one-directory-at-a-time listing) lets the
agent navigate to the part it wants instead. Per-tool caps therefore stay
tighter than this ceiling — the ceiling only has to catch what nobody
capped.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from agents import FunctionTool, ToolOutputFileContent, ToolOutputImage, ToolOutputText
from agents.tool_guardrails import (
    ToolGuardrailFunctionOutput,
    ToolOutputGuardrail,
    ToolOutputGuardrailData,
)

from .context import AgentToolContext

TOOL_OUTPUT_CAP_NAME = "tool_output_cap"

# Binary payloads (an image from `read_image`, a file attachment) are not text
# in the context window, and slicing one corrupts it — so the ceiling leaves
# them alone. `ToolOutputText` is deliberately NOT here: it IS text, and
# exempting it would put the backstop back at the mercy of the next tool
# author, which is the thing it exists to replace.
_STRUCTURED = (ToolOutputImage, ToolOutputFileContent)

_NARROW_HINT = "narrow the command (e.g. grep/head/tail/wc) to see the part you need"


def truncate_middle(text: str, max_chars: int, *, hint: str = _NARROW_HINT) -> str:
    """Cap `text` at `max_chars` keeping the HEAD and the TAIL (issue #44).

    A `grep`/log dump's useful bits cluster at both ends — the first
    matches up top, the count / error / summary at the bottom — so a
    head-only cut throws away the punchline. We keep ~2/3 of the budget
    for the head, ~1/3 for the tail, trim each to a line boundary, and
    drop a marker in between that tells the agent how to get the rest.
    """
    if len(text) <= max_chars:
        return text
    head_budget = max_chars * 2 // 3
    tail_budget = max_chars - head_budget
    head = text[:head_budget]
    nl = head.rfind("\n")
    if nl > 0:  # cut on a line boundary so we don't split a line mid-token
        head = head[:nl]
    tail = text[len(text) - tail_budget :]
    nl = tail.find("\n")
    # Trim the partial first line — unless that would eat the tail whole, which
    # is exactly what a one-line body with a trailing newline does. head+tail is
    # the point (#44): the punchline lives at the end.
    if nl != -1 and nl + 1 < len(tail):
        tail = tail[nl + 1 :]
    omitted = len(text) - len(head) - len(tail)
    marker = f"\n\n… [{omitted} chars omitted — {hint}] …\n\n"
    return head + marker + tail


def _rendered(output: Any) -> str | None:
    """The text the SDK will actually put in the history for `output`, or
    ``None`` when it is a structured payload we must leave alone.

    Mirrors ``agents.items.ItemHelpers._convert_tool_output``: a list whose
    items are ALL structured stays structured; anything else is ``str()``-ed.
    That is why the cap measures ``str(output)`` and not, say, the number of
    entries a listing tool returned — the repr of a 20k-element list is what
    reaches the model.
    """
    if isinstance(output, ToolOutputText):
        return output.text
    if isinstance(output, _STRUCTURED):
        return None
    if (
        isinstance(output, list | tuple)
        and output
        and all(isinstance(i, _STRUCTURED) for i in output)
    ):
        return None
    return str(output)


def _cap_output(data: ToolOutputGuardrailData) -> ToolGuardrailFunctionOutput:
    ctx = data.context.context
    assert isinstance(ctx, AgentToolContext)
    rendered = _rendered(data.output)
    if rendered is None or len(rendered) <= ctx.tool_output_max_chars:
        return ToolGuardrailFunctionOutput.allow()
    return ToolGuardrailFunctionOutput.reject_content(
        truncate_middle(
            rendered,
            ctx.tool_output_max_chars,
            hint=(
                "this tool answered with more than the context can hold — ask it for a "
                "narrower slice (a sub-path, a filter, a page) instead of the whole thing"
            ),
        ),
        output_info={"tool": data.context.tool_name, "rendered_chars": len(rendered)},
    )


def cap_tool_outputs(tools: list[FunctionTool]) -> list[FunctionTool]:
    """Attach the output ceiling to every tool in `tools`, preserving whatever
    guardrails a tool already declares. Idempotent — re-wrapping an already
    capped toolset does not stack a second copy."""
    capped: list[FunctionTool] = []
    for tool in tools:
        existing = list(tool.tool_output_guardrails or [])
        if any(g.get_name() == TOOL_OUTPUT_CAP_NAME for g in existing):
            capped.append(tool)
            continue
        guardrail: ToolOutputGuardrail[Any] = ToolOutputGuardrail(
            guardrail_function=_cap_output, name=TOOL_OUTPUT_CAP_NAME
        )
        capped.append(dataclasses.replace(tool, tool_output_guardrails=[*existing, guardrail]))
    return capped
