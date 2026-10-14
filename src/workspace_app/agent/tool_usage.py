"""Counting every AI tool call (docs/plan-tool-usage-stats.md).

`_agent_for` wraps every tool it hands the model — built-ins, the rule-granted
cards, package commands — so every turn on every surface (app chat, KB chat,
workflow steps, schedules, sub-agents, the KB sub-agent, background agents)
counts its calls in one place, once each. The wrap measures the call, judges
whether it failed (§3.3), and adds it to the turn's recorder; the recorder
keeps the day's sums in memory until a flush writes them (§3.5).

A turn without a recorder (`ctx.tool_usage is None`: tests, tools called off
the runner) counts nothing and runs exactly as before.
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from .args_recovery import _SDK_TOOL_ERROR_PREFIX

if TYPE_CHECKING:
    from agents import FunctionTool
    from agents.tool_context import ToolContext

    from .context import AgentToolContext

#: Where a tool came from (D6). Decided where `_agent_for` still holds each
#: source in its own list — never guessed from the name, because a first-party
#: package command and a built-in are named alike.
ToolSource = Literal["builtin", "first_party", "third_party"]

#: Who a call is attributed to when the turn names nobody — a background agent
#: (wiki maintenance, card drafting, compaction).
NOBODY = "(系統)"

#: Joins a surface and an App into one breakdown key; a character no slug or
#: surface name contains.
SEP = "∕"

_EXIT = re.compile(r"Tool `[^`]*` returned \(exit_code=(-?\d+)\)")


def is_failure(result: object) -> bool:
    """Whether a tool's reply is a failure (§3.3) — the shapes failures
    actually take here, since there is no one flag:

    - the SDK's string for an exception inside a tool;
    - a command that exited non-zero (`_format_exec`'s header);
    - an in-band refusal, which every built-in writes as `error:` / `Error:`.

    A structured output (an image, a list of parts) is a success."""
    if not isinstance(result, str):
        return False
    if result.startswith(_SDK_TOOL_ERROR_PREFIX):
        return True
    if (m := _EXIT.match(result)) is not None:
        return int(m.group(1)) != 0
    return result.lstrip().lower().startswith("error:")


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class ToolUsageRow:
    """One tool's sums for one day — what a flush writes. Each breakdown maps
    a key to `[calls, fails, total_ms]`."""

    day: str
    tool: str
    source: str
    calls: int = 0
    fails: int = 0
    total_ms: int = 0
    by_user: dict[str, list[int]] = field(default_factory=dict)
    by_surface_app: dict[str, list[int]] = field(default_factory=dict)
    by_item: dict[str, list[int]] = field(default_factory=dict)


def _bump(table: dict[str, list[int]], key: str, failed: bool, ms: int) -> None:
    cell = table.setdefault(key, [0, 0, 0])
    cell[0] += 1
    cell[1] += int(failed)
    cell[2] += ms


class ToolUsageRecorder:
    """This process's tool-call sums since the last `drain`. Thread-safe:
    tools run on the event loop, but a flush may drain from another thread."""

    def __init__(
        self,
        *,
        day: Callable[[], str] = _utc_day,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._day = day
        self.clock = clock
        self._rows: dict[tuple[str, str, str], ToolUsageRow] = {}
        self._lock = threading.Lock()

    def record(
        self,
        tool: str,
        source: str,
        *,
        failed: bool,
        ms: int,
        user: str,
        surface: str,
        app: str,
        item: str,
    ) -> None:
        day = self._day()
        with self._lock:
            row = self._rows.get((day, tool, source))
            if row is None:
                row = self._rows[(day, tool, source)] = ToolUsageRow(day, tool, source)
            row.calls += 1
            row.fails += int(failed)
            row.total_ms += ms
            _bump(row.by_user, user, failed, ms)
            _bump(row.by_surface_app, f"{surface}{SEP}{app}", failed, ms)
            _bump(row.by_item, item, failed, ms)

    def drain(self) -> list[ToolUsageRow]:
        """Every row counted since the last drain, handed over once."""
        with self._lock:
            rows, self._rows = list(self._rows.values()), {}
        return rows


def who(actx: AgentToolContext) -> str:
    """The person a call is attributed to (§3.4): the one the turn's builder
    named for the statistics, else the speaker, else the acting user."""
    if actx.usage_user:
        return actx.usage_user
    if actx.speaker is not None:
        return actx.speaker.id
    return actx.acting_user or NOBODY


def wrap_with_usage(tool: FunctionTool, source: str) -> FunctionTool:
    """`tool`, counted: each call is timed and judged, and added to the
    turn's recorder. Wraps OUTSIDE the argument recovery, so a call whose
    arguments the tool never saw is counted too — as a failure: the model
    asked for the tool and it did not run."""
    original = tool.on_invoke_tool

    # `ToolContext`, like the argument recovery's wrap: the SDK reads this
    # annotation to decide whether to downgrade the context it passes.
    async def counted(ctx: ToolContext[AgentToolContext], args_json: str) -> Any:
        actx = ctx.context
        rec = actx.tool_usage
        if rec is None:
            return await original(ctx, args_json)
        start = rec.clock()
        failed = True
        try:
            result = await original(ctx, args_json)
            failed = is_failure(result)
            return result
        finally:
            rec.record(
                tool.name,
                source,
                failed=failed,
                ms=round((rec.clock() - start) * 1000),
                user=who(actx),
                surface=actx.surface,
                app=actx.app_slug or "",
                item=actx.investigation_id or "",
            )

    return dataclasses.replace(tool, on_invoke_tool=counted)
