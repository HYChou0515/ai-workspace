"""The reduction policies a deployment can choose between (#624).

None of these is privileged by the request path. ``drop-oldest`` is the
incumbent — it is what the code did before the seam existed, kept as a named
strategy so introducing the seam changes no behaviour by itself. The other two
exist because the measured evidence says the incumbent spends the budget badly,
and because "which of these do we want" is a product judgement that belongs in
configuration rather than in an `if` somewhere in the send path.

Adding a summarising / compacting policy later means adding a class here, not
touching the request path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .context_reduce import Estimator, IContextReducer, ReductionResult

#: Tool output larger than this is what a reducer elides first — it is the bulk
#: of a working session's context and the least valuable to re-read verbatim
#: (a 30 KB `exec` dump costs roughly twenty turns of dialogue).
_BULKY_TOOL_CHARS = 2_000


def _fits(messages: Sequence[Any], budget: int, estimate: Estimator) -> bool:
    return estimate(messages) <= budget


def _keep_newest_that_fit(messages: Sequence[Any], budget: int, estimate: Estimator) -> list[Any]:
    """The newest contiguous suffix that fits, always keeping at least one — the
    turn's own context is the last thing worth losing."""
    kept: list[Any] = []
    for m in reversed(messages):
        if kept and not _fits([m, *kept], budget, estimate):
            break
        kept.insert(0, m)
    return kept or list(messages[-1:])


class DropOldest(IContextReducer):
    """Drop from the front until it fits — the incumbent policy.

    Its cost is worth stating plainly: it sacrifices the oldest messages first,
    which in practice means the user's opening request (the task) goes before a
    single byte of any tool dump. It is kept because it is what the system did,
    not because it is a good default.
    """

    name = "drop-oldest"

    def reduce(
        self, messages: Sequence[Any], *, budget: int, estimate: Estimator
    ) -> ReductionResult:
        msgs = list(messages)
        if not msgs or _fits(msgs, budget, estimate):
            return ReductionResult(messages=msgs)
        kept = _keep_newest_that_fit(msgs, budget, estimate)
        dropped = len(msgs) - len(kept)
        return ReductionResult(
            messages=kept,
            summary=f"較早的 {dropped} 則訊息已超出 AI 一次能讀的範圍,不會被讀到。",
            changed=True,
        )


class ElideToolOutput(IContextReducer):
    """Shrink bulky tool output before giving up any conversation.

    A session's context is dominated by tool dumps, not by what anyone said. The
    fact that a command ran and roughly what it returned is usually enough for
    the model to stay oriented, so replacing the body of an old dump buys back
    far more of the session than dropping the same number of tokens' worth of
    dialogue. Only if eliding is not enough does it fall back to dropping.
    """

    name = "elide-tool-output"

    def reduce(
        self, messages: Sequence[Any], *, budget: int, estimate: Estimator
    ) -> ReductionResult:
        msgs = list(messages)
        if not msgs or _fits(msgs, budget, estimate):
            return ReductionResult(messages=msgs)
        elided = 0
        # Oldest first: the most recent tool output is the one the current turn
        # is most likely still reasoning about.
        for i, m in enumerate(msgs[:-1]):
            if _fits(msgs, budget, estimate):
                break
            content = getattr(m, "content", "") or ""
            if getattr(m, "role", "") == "tool" and len(content) > _BULKY_TOOL_CHARS:
                msgs[i] = _Elided(m, content)
                elided += 1
        if _fits(msgs, budget, estimate):
            return ReductionResult(
                messages=msgs,
                summary=f"{elided} 筆較早的工具輸出已摺疊(只留下摘要),對話內容完整保留。",
                changed=True,
            )
        kept = _keep_newest_that_fit(msgs, budget, estimate)
        dropped = len(msgs) - len(kept)
        return ReductionResult(
            messages=kept,
            summary=(
                f"{elided} 筆較早的工具輸出已摺疊;仍放不下,另有 {dropped} 則較早的訊息不會被讀到。"
            ),
            changed=True,
        )


class KeepTask(IContextReducer):
    """Never sacrifice the opening request; drop from the middle instead.

    Every later turn refers back to the task ("寫成報告", "那第 3 批呢"), so a
    thread that has lost its first message reads as an agent that forgot what it
    was doing — the exact complaint this issue started from.
    """

    name = "keep-task"

    def reduce(
        self, messages: Sequence[Any], *, budget: int, estimate: Estimator
    ) -> ReductionResult:
        msgs = list(messages)
        if not msgs or _fits(msgs, budget, estimate):
            return ReductionResult(messages=msgs)
        first = next((m for m in msgs if getattr(m, "role", "") == "user"), None)
        if first is None or first is msgs[-1]:
            return DropOldest().reduce(msgs, budget=budget, estimate=estimate)
        head_cost = estimate([first])
        tail = _keep_newest_that_fit(msgs[1:], max(0, budget - head_cost), estimate)
        kept = [first, *tail]
        dropped = len(msgs) - len(kept)
        return ReductionResult(
            messages=kept,
            summary=(f"中間 {dropped} 則訊息不會被讀到(你最初的需求與最近的對話都保留)。"),
            changed=True,
        )


class ElideThenKeepTask(IContextReducer):
    """Fold bulky old tool output first; if that is not enough, drop from the
    middle rather than sacrifice the task. The evidence-backed default (#624).

    Both halves come from measurement, not taste. Tool output is the bulk of a
    working session's context (one 30 KB `exec` dump ≈ twenty turns of
    dialogue) and the least valuable to re-read verbatim — the model needs to
    know a command ran and roughly what came back, not the 8,000th row. And the
    opening request is what every later turn refers back to, so a thread that
    has lost it reads as an agent that forgot what it was doing, which is the
    complaint this whole issue began from.

    `drop-oldest` remains one config line away for a deployment that would
    rather re-read old output verbatim than keep the conversation.
    """

    name = "elide-then-keep-task"

    def reduce(
        self, messages: Sequence[Any], *, budget: int, estimate: Estimator
    ) -> ReductionResult:
        msgs = list(messages)
        if not msgs or _fits(msgs, budget, estimate):
            return ReductionResult(messages=msgs)
        folded = ElideToolOutput().reduce(msgs, budget=budget, estimate=estimate)
        # Eliding alone did it — every message survives, some are shorter.
        if len(folded.messages) == len(msgs) and _fits(folded.messages, budget, estimate):
            return folded
        # Still over: give up conversation, but never the task.
        kept = KeepTask().reduce(_elide_all_bulky(msgs), budget=budget, estimate=estimate)
        return ReductionResult(
            messages=kept.messages,
            summary=f"較早的工具輸出已摺疊;{kept.summary}",
            changed=True,
        )


def _elide_all_bulky(messages: Sequence[Any]) -> list[Any]:
    """Fold every bulky tool output except the newest message's."""
    out = list(messages)
    for i, m in enumerate(out[:-1]):
        content = getattr(m, "content", "") or ""
        if getattr(m, "role", "") == "tool" and len(content) > _BULKY_TOOL_CHARS:
            out[i] = _Elided(m, content)
    return out


class _Elided:
    """A tool message whose body has been folded away, keeping the fact that it
    ran. Deliberately duck-typed rather than a `Message` — reducers work on
    whatever the caller hands them."""

    def __init__(self, original: Any, content: str) -> None:
        self.role = getattr(original, "role", "tool")
        self.tool_name = getattr(original, "tool_name", None)
        self.tool_call_id = getattr(original, "tool_call_id", None)
        self.tool_args = getattr(original, "tool_args", None)
        name = self.tool_name or "tool"
        self.content = f"[{name} 的輸出({len(content):,} 字元)已摺疊,如需重看請重新執行]"


REDUCERS: dict[str, IContextReducer] = {
    r.name: r for r in (DropOldest(), ElideToolOutput(), KeepTask(), ElideThenKeepTask())
}

#: The policy applied when configuration names none — chosen on this issue's
#: own measurements, not left open. Defaulting to the incumbent would have meant
#: shipping the behaviour #624 exists to fix: a session that loses its task
#: statement while keeping a tool dump twenty times its size. Every other policy,
#: including the incumbent, stays one config line away.
DEFAULT_REDUCER = ElideThenKeepTask.name


def get_reducer(name: str) -> IContextReducer:
    """The named policy. Unknown names raise rather than falling back — a
    silently substituted policy is how an unchosen behaviour ends up governing
    a deployment, which is the defect this seam exists to prevent."""
    try:
        return REDUCERS[name]
    except KeyError:
        raise ValueError(
            f"unknown context-reduction strategy {name!r}; known: {sorted(REDUCERS)}"
        ) from None
