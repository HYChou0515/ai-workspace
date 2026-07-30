"""#615 P5: the morning hand-over — what happened while you were away.

An overnight run that ends silently is the same as one that never happened: you
come in, see a thread you did not read, and have to reconstruct it yourself.
So every unattended ending writes ONE summary into the chat and rings the bell.

Four endings, four different things a person needs to do next, so they are
worded differently rather than sharing a generic "run finished":

* ``met`` — nothing to do; here is what it did.
* ``stalled`` — it got stuck; it needs you to look.
* ``exhausted`` — it ran out of budget mid-way; it needs a decision, not a fix.
* ``window`` — the window closed with work left; it will carry on tonight.

One cheap LLM call, blocking (`ILlm` streams underneath, so the always-stream
rule holds inside `collect`); the driver runs it off the loop like the checker.
Summarising is best-effort: a failed call must not swallow the ending itself,
so the caller still gets a marker built from the outcome alone.
"""

from __future__ import annotations

import logging

from ..kb.llm import ILlm
from ..resources.conversation import Message

logger = logging.getLogger(__name__)

# Enough of the night to summarise; small enough to stay a cheap call.
_TAIL_MESSAGES = 40
_TAIL_CHARS = 12_000

ENDINGS = ("met", "stalled", "exhausted", "window")

_HEADLINE = {
    "met": "目標已達成",
    "stalled": "卡住了,需要你看一下",
    "exhausted": "額度用盡,還沒完成",
    "window": "上班時間到了,先停在這裡",
}

_NEXT_STEP = {
    "met": "不需要你做什麼,以下是它做了什麼。",
    "stalled": "它連續幾輪沒有進展,已經停下來等你。",
    "exhausted": "自動續跑的額度用完了,要不要繼續由你決定。",
    "window": "還沒做完,今天晚上會自己接著做。",
}

_PROMPT = """\
你是一位交班的同事。以下是一個 AI 在使用者離開期間、為了這個目標所做的工作紀錄。

目標:{condition}
結束原因:{reason}

請用繁體中文寫一段**給早上回來的人看的**交班摘要,不要超過六句話,依序講清楚:
1. 實際完成了什麼(只寫紀錄裡真的發生的事,不要寫計畫或打算)
2. 動了哪些檔案或做了哪些明確的改動
3. 為什麼停在這裡
4. 哪裡需要這個人接手決定

工作紀錄(最舊在前):
{transcript}

只輸出摘要本文,不要標題、不要條列符號。"""


def night_transcript(messages: list[Message]) -> str:
    """The part of the thread the summary is about."""
    shown = [m for m in messages if m.role in ("user", "assistant", "tool")][-_TAIL_MESSAGES:]
    lines = [f"{m.role}: {m.content}" for m in shown if m.content]
    return "\n".join(lines)[-_TAIL_CHARS:]


def headline(ending: str, condition: str) -> str:
    """The one line that is true even if the model never answers."""
    return f"{_HEADLINE.get(ending, '已結束')}:{condition}"


def write_summary(llm: ILlm, *, condition: str, ending: str, transcript: str) -> str:
    """The hand-over body. Returns "" when the model gives nothing usable —
    the caller still has `headline`, so a summariser outage costs detail, never
    the notification itself."""
    try:
        out = llm.collect(
            _PROMPT.format(
                condition=condition,
                reason=_HEADLINE.get(ending, ending),
                transcript=transcript,
            ),
            recover_reasoning=True,
        )
    except Exception:  # noqa: BLE001 — best-effort; the ending still gets reported
        logger.exception("goal wrap-up summary failed (%s)", ending)
        return ""
    return out.strip()


def marker_text(ending: str, condition: str, summary: str) -> str:
    """What lands in the thread: the outcome, what to do next, then the story."""
    parts = [headline(ending, condition), _NEXT_STEP.get(ending, "")]
    if summary:
        parts.append(summary)
    return "\n\n".join(p for p in parts if p)
