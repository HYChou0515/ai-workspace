"""Turning a span of conversation into a précis (#739).

The incumbent answer to a full window is `LayeredReducer`: fold the bulky tool
output, drop the middle, and — last — drop the user's opening request, then tell
them to start a new chat. That makes the user carry the cost of the ceiling:
re-explain the background, re-paste the paths, re-list the dead ends.

Compaction is the other answer. The span is replaced by a summary and the
conversation continues. Crucially it is NOT deletion: `history_items` simply
starts replaying at the newest `SUMMARY_ROLE` message, so the original messages
stay in the store and the user can still scroll back through all of them.

The summary is written by a sub-agent with an EMPTY context, for the same reason
`ask_knowledge_base` delegates (#270): the span being compacted is the noisiest
input in the system — it is being compacted precisely because it no longer fits
— so reading it back into the caller's own context to summarise it would blow
the window open in order to reclaim it.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import msgspec.structs

from ..agent.context import AgentToolContext
from ..context_budget import SUMMARY_ROLE
from ..context_reduce import Estimator
from ..context_reducers import fold_bulky
from .events import MessageDelta
from .runner import AgentRunner

#: What the summariser is told to preserve. Ordered by what hurts most to lose:
#: the original request is what every later message refers back to (and is the
#: first thing the layered reducer sacrifices), and a paraphrased path or id is
#: worse than none at all because the next turn acts on it.
_INSTRUCTIONS = """以下是一段對話紀錄。請把它整理成一段摘要,讓接手的人不必讀原文也能繼續工作。

必須保留:
1. 使用者**最初**交代的任務 —— 用原話,不要改寫。後面每一句都在指涉它。
2. 已經做完的事,以及結論。
3. **未完成**的事、還沒驗證的假設、待辦。
4. 檔案路徑、id、指令、錯誤訊息 —— **逐字**照抄,不要改寫或簡稱。

不要照抄工具輸出的內容,只留它得出的結論。不要加上原文沒有的判斷。
直接輸出摘要本身,不要前言、不要說明你在做什麼。

--- 對話紀錄開始 ---
"""


def _line(m: Any) -> str:
    """One transcript line. A tool message is labelled by the tool that produced
    it — "which tool said this" is often the only thing worth keeping about it."""
    role = getattr(m, "role", "") or "?"
    name = getattr(m, "tool_name", None)
    who = f"{role}({name})" if role == "tool" and name else role
    return f"[{who}] {getattr(m, 'content', '') or ''}"


def compaction_prompt(messages: Sequence[Any]) -> str:
    """The whole instruction handed to the summariser, transcript included."""
    body = "\n".join(_line(m) for m in messages)
    return f"{_INSTRUCTIONS}{body}\n--- 對話紀錄結束 ---"


def _after_last_summary(messages: Sequence[Any]) -> tuple[int, list[Any]]:
    """The slice starting at the newest summary, and where it starts.

    A second compaction covers what happened SINCE the first one. Reading back
    past it would summarise a summary — each pass a copy of a copy, with the
    user's original request decaying a little every time."""
    msgs = list(messages)
    for i in range(len(msgs) - 1, -1, -1):
        if getattr(msgs[i], "role", "") == SUMMARY_ROLE:
            return i, msgs[i:]
    return 0, msgs


def split_for_compaction(
    messages: Sequence[Any], *, keep_tokens: int, estimate: Estimator
) -> tuple[list[Any], list[Any]]:
    """Split a thread into ``(to_summarise, to_keep)``.

    The tail is bounded in TOKENS, not in messages. "Keep the last three" sounds
    modest until three `exec` dumps arrive — the tail alone would then overflow
    the window, so compaction would run, cost a turn and change nothing. At
    least one message always survives regardless: a summary with no conversation
    after it is not a conversation.

    The span never reaches back past an earlier summary (see
    ``_after_last_summary``), and an empty first element means there is nothing
    worth compacting — the caller checks that BEFORE spending an LLM call."""
    _, msgs = _after_last_summary(messages)
    kept: list[Any] = []
    total = 0
    for m in reversed(msgs):
        cost = estimate([m])
        if kept and total + cost > max(0, keep_tokens):
            break
        kept.append(m)
        total += cost
    kept.reverse()
    return msgs[: len(msgs) - len(kept)], kept


def compaction_plan(
    messages: Sequence[Any], *, keep_tokens: int, estimate: Estimator
) -> tuple[int, list[Any]]:
    """``(insert_at, span)`` — where the summary goes, and what it replaces.

    The summary is INSERTED before the kept tail, never appended: appended, it
    would sit after the newest messages, and `history_items` would replay it as
    the latest thing said while dropping the very turns it was meant to precede.

    ``insert_at`` is an index into the list the caller passed, because that is
    the list the caller mutates."""
    start, _ = _after_last_summary(messages)
    span, kept = split_for_compaction(messages, keep_tokens=keep_tokens, estimate=estimate)
    return start + len(span), span


#: Share of the room actually available that the kept tail may occupy —
#: `min(budget - overhead, what the thread's own messages cost)`, not the
#: whole budget. The `min` is what makes a forced pass on a roomy window do
#: anything at all.
#:
#: Not a knob. Too large and the thread is over budget again on the next turn,
#: paying a round trip every time; too small and we throw away the recent
#: exchange the user's next message is about. Half leaves room for the summary
#: itself and somewhere for the conversation to grow before the next pass.
KEEP_TAIL_RATIO = 0.5


def plan_for_budget(
    messages: Sequence[Any],
    *,
    used: int,
    budget: int | None,
    estimate: Estimator,
    force: bool = False,
) -> tuple[int, list[Any]]:
    """``(insert_at, span)`` for a thread that no longer fits — empty when it does.

    The trigger is deliberately not a new threshold: it is the moment the
    reducer would otherwise start throwing things away. One number to get wrong
    instead of two, and it cannot drift from what actually happens.

    ``force`` is a person pressing compact. They have a reason we do not: the
    last hour of debugging is finished and they want the window back BEFORE the
    next question rather than after it stops fitting. So the budget gates the
    automatic path only — asking is the whole trigger.

    ``budget is None`` means no ceiling is known, and #624's rule there is to
    send everything and learn the real limit from the response. A thread that is
    never trimmed must never be compacted either — spending a turn and losing
    detail for a limit nobody measured is the worse of the two mistakes."""
    # Nothing to measure on a thread that already fits — and most do. Folding
    # plus three passes of the CJK estimator on every send put ~98 ms in front
    # of the overwhelming majority of turns; this comparison is free.
    if not force and (budget is None or used <= budget):
        return 0, []

    # Everything from here is scoped to the LIVE thread — the slice from the
    # newest summary on. `used` is scoped that way too (`context_usage` cuts
    # there), and mixing the two scopes is what killed automatic compaction
    # after the first pass: the saving was measured over the whole store, which
    # still holds every pre-summary dump, so `used - freed` came out small — and
    # sometimes negative — forever after.
    start, live = _after_last_summary(messages)
    folded_live = fold_bulky(live)

    # The free stage, second. `history_items` folds bulky tool output at turn
    # time anyway, so a thread pushed over the line by one `exec` dump is
    # already saved; spending a round trip AND permanently replacing a span to
    # reclaim what folding gives away is the expensive answer to a free
    # question. This is the agreed order: 折疊 → 壓縮 → 丟棄.
    own = estimate(folded_live)
    freed = max(0, estimate(live) - own)
    if not force and budget is not None and used - freed <= budget:
        return 0, []

    overhead = max(0, used - freed - own)
    room = own if budget is None else budget - overhead
    if room <= 0:
        # The fixed overhead alone exceeds the budget, so there is no room for
        # ANY history and compaction cannot make one. Left to run, it sizes the
        # tail against a negative room, razes the thread to its last message,
        # pays a round trip and recovers nothing that was ever the problem. The
        # operator has to raise the window or cut the prompt; amputating the
        # user's conversation does not help.
        #
        # This binds `force` as well. Asking is the trigger, but it is not a
        # licence to do harm — and with the automatic path silent here, the
        # button would otherwise be the only thing still acting, trading the
        # whole conversation for a summary that provably cannot make it fit.
        return 0, []

    # From here the FOLDED live thread is the subject. Folding preserves message
    # count and order, so the insert index still addresses the caller's own list
    # — but the span handed to the summariser carries markers instead of dumps,
    # which is the only thing bounding that request. Nothing else does: the span
    # grows the further over budget the thread is, it goes out as one prompt
    # beside the whole system prompt, and if it overflows the runner can only
    # shrink `ctx.history` — which the compactor deliberately emptied. It would
    # return nothing, and the thread would fall back to the amputation this
    # exists to remove, most likely exactly when it is most needed.
    keep_tokens = int(max(0, min(room, own)) * KEEP_TAIL_RATIO)
    span, kept = split_for_compaction(folded_live, keep_tokens=keep_tokens, estimate=estimate)
    return start + len(span), span


class IConversationCompactor(abc.ABC):
    """Replace a span of a conversation with a précis of it."""

    @abc.abstractmethod
    async def summarise(self, messages: Sequence[Any], *, ctx: AgentToolContext) -> str:
        """Return the summary text standing in for `messages`.

        Returning `""` means "no summary" and the caller must NOT compact on it:
        replacing a span with nothing is strictly worse than the truncation this
        exists to avoid."""


class AgentCompactor(IConversationCompactor):
    """Summarise through the same agent runner a turn uses, so the précis is
    written by the model the conversation is already running on.

    Deliberately not a separately configured endpoint: a deployment that chats
    on a capable model and summarises on a cheap one degrades in a way nobody
    can see — the chat simply seems to get stupider after a while, with no error
    and nothing in the logs to point at."""

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

    async def summarise(self, messages: Sequence[Any], *, ctx: AgentToolContext) -> str:
        if not list(messages):
            return ""
        # No tools, explicitly. `[]` means "none"; `None` would mean "use the
        # workspace defaults", which is the opposite. The sub-context has no
        # sandbox, filestore or retriever, so any tool the model reached for
        # could not work — but reaching for one produces no text, `summarise`
        # returns "", and the caller reads that as "no summary" and falls back
        # to the amputation. A silent no-op from a capability that never existed.
        cfg = ctx.agent_config
        sub = replace(
            ctx,
            history=[],
            turn_image_urls=(),
            agent_config=msgspec.structs.replace(cfg, allowed_tools=[]) if cfg else None,
        )
        parts: list[str] = []
        async for ev in self._runner.run(compaction_prompt(messages), sub):
            if isinstance(ev, MessageDelta):
                parts.append(ev.text)
        return "".join(parts).strip()
