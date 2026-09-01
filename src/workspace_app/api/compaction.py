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

from ..agent.context import AgentToolContext
from ..context_budget import SUMMARY_ROLE
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


def split_for_compaction(
    messages: Sequence[Any], *, keep_recent: int
) -> tuple[list[Any], list[Any]]:
    """Split a thread into ``(to_summarise, to_keep)``.

    Two rules, both about not making things worse:

    The newest ``keep_recent`` messages are never touched. The user's next
    message almost always refers to the last few turns, and a précis of "what we
    just said" is the least useful summary and the most costly to blur. It also
    BOUNDS the summariser's input — the span is everything except that tail, so
    it can never be a whole window, which is what makes a single pass enough.

    And the span never reaches back past an earlier summary. A second compaction
    covers what happened SINCE the first; re-reading past it would summarise a
    summary, each pass copying a copy until the original request has quietly
    decayed into nothing.

    An empty first element means there is nothing worth compacting — the caller
    checks that BEFORE spending an LLM call.
    """
    msgs = list(messages)
    for i in range(len(msgs) - 1, -1, -1):
        if getattr(msgs[i], "role", "") == SUMMARY_ROLE:
            msgs = msgs[i:]
            break
    cut = max(0, len(msgs) - max(0, keep_recent))
    return msgs[:cut], msgs[cut:]


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
        sub = replace(ctx, history=[], turn_image_urls=())
        parts: list[str] = []
        async for ev in self._runner.run(compaction_prompt(messages), sub):
            if isinstance(ev, MessageDelta):
                parts.append(ev.text)
        return "".join(parts).strip()
