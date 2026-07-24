"""The reduction policy this system runs (#624).

There is exactly ONE, deliberately. An earlier cut shipped four selectable
policies, which was over-built in the same way the original defect was
under-built: three of them would never have run anywhere, so three of them
would have rotted. The interface exists because a *second* implementation is
foreseeable — summarising a span rather than giving it up — not because a menu
was needed today.

The algorithm gives things up in increasing order of what they cost you, and
each stage runs only because the one before it was not enough:

  1. **Fold bulky OLD tool output.** The cheapest sacrifice by a wide margin.
     Tool output is the bulk of a working session — one 30 KB ``exec`` is
     roughly twenty turns of dialogue — and nobody re-reads row 8,000; the model
     needs to know a command ran and roughly what came back. Measured on a
     six-turn session: 12,153 tokens collapse to 279 with every message still
     present. That is why it goes first — it is almost always enough on its own,
     so the later stages rarely run at all.
  2. **Drop from the middle, keeping the task.** Costs conversation, but never
     the opening request, which every later turn refers back to. A thread that
     has lost it reads as an agent that forgot what it was doing — the complaint
     this whole issue began from.
  3. **Give up the task too.** Only when the request plus the current turn will
     not fit together. The newest message always survives: a turn without its
     own context produces an incoherent reply rather than a merely forgetful one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .context_reduce import Estimator, IContextReducer, ReductionResult

#: Tool output larger than this is folded first — it is the bulk of a session's
#: context and the least valuable to re-read verbatim.
_BULKY_TOOL_CHARS = 2_000


def _role(m: Any) -> str:
    """The role of a message in either shape the system carries it in: a
    persisted ``Message`` object or an SDK input item (a plain dict).

    Understanding only one of them would not fail — it would silently do nothing
    on the other, which is exactly how a reduction once reported success while
    dropping the user's task."""
    return m.get("role", "") if isinstance(m, dict) else (getattr(m, "role", "") or "")


def _content(m: Any) -> str:
    if isinstance(m, dict):
        return str(m.get("content", "") or "")
    return getattr(m, "content", "") or ""


def _fits(messages: Sequence[Any], budget: int, estimate: Estimator) -> bool:
    return estimate(messages) <= budget


def _keep_newest_that_fit(messages: Sequence[Any], budget: int, estimate: Estimator) -> list[Any]:
    """The newest contiguous suffix that fits, always keeping at least one."""
    kept: list[Any] = []
    for m in reversed(messages):
        if kept and not _fits([m, *kept], budget, estimate):
            break
        kept.insert(0, m)
    return kept or list(messages[-1:])


class _Folded:
    """A tool message whose body has been folded away, keeping the fact that it
    ran. Duck-typed rather than a `Message` — reducers work on whatever the
    caller hands them."""

    def __init__(self, original: Any, content: str) -> None:
        self.role = _role(original) or "tool"
        self.tool_name = getattr(original, "tool_name", None)
        self.tool_call_id = getattr(original, "tool_call_id", None)
        self.tool_args = getattr(original, "tool_args", None)
        name = self.tool_name or "tool"
        self.content = f"[{name} 的輸出({len(content):,} 字元)已摺疊,如需重看請重新執行]"


def _fold(original: Any, content: str) -> Any:
    """Fold one bulky tool output, preserving the shape it arrived in so SDK
    history keeps its wire form."""
    folded = _Folded(original, content)
    if isinstance(original, dict):
        return {**original, "content": folded.content}
    return folded


def _fold_bulky(messages: Sequence[Any]) -> list[Any]:
    """Stage 1 — fold every bulky tool output except the newest message's (the
    current turn is most likely still reasoning about that one)."""
    out = list(messages)
    for i, m in enumerate(out[:-1]):
        content = _content(m)
        if _role(m) == "tool" and len(content) > _BULKY_TOOL_CHARS:
            out[i] = _fold(m, content)
    return out


class LayeredReducer(IContextReducer):
    """Fold, then drop the middle, then — last of all — the task itself.

    See the module docstring for why the order is what it is: each stage is
    strictly more expensive than the one before, so the cheap sacrifice is
    always exhausted before an expensive one is considered.
    """

    name = "layered"

    def reduce(
        self, messages: Sequence[Any], *, budget: int, estimate: Estimator
    ) -> ReductionResult:
        msgs = list(messages)
        if not msgs or _fits(msgs, budget, estimate):
            return ReductionResult(messages=msgs)

        # ── 1. fold bulky old tool output ──────────────────────────
        folded = _fold_bulky(msgs)
        n = sum(1 for a, b in zip(folded, msgs, strict=False) if _content(a) != _content(b))
        if _fits(folded, budget, estimate):
            return ReductionResult(
                messages=folded,
                summary=f"{n} 筆較早的工具輸出已摺疊(只留下摘要),對話內容完整保留。",
                changed=True,
            )

        # ── 2. drop from the middle, keeping the task ──────────────
        task = next((m for m in folded if _role(m) == "user"), None)
        if task is not None and task is not folded[-1]:
            tail = _keep_newest_that_fit(folded[1:], max(0, budget - estimate([task])), estimate)
            kept = [task, *tail]
            if _fits(kept, budget, estimate):
                dropped = len(folded) - len(kept)
                note = f"{n} 筆較早的工具輸出已摺疊;" if n else ""
                return ReductionResult(
                    messages=kept,
                    summary=(
                        f"{note}另有中間 {dropped} 則訊息不會被讀到"
                        "(你最初的需求與最近的對話都保留)。"
                    ),
                    changed=True,
                )

        # ── 3. not even the task fits ──────────────────────────────
        return ReductionResult(
            messages=[msgs[-1]],
            summary=(
                "這一輪的內容就已經接近模型能讀的上限,先前的對話(包含你最初的需求)都不會被讀到。"
            ),
            changed=True,
        )


_REDUCER = LayeredReducer()


def default_reducer() -> IContextReducer:
    """The reduction policy. One implementation today; the interface is here for
    the second one (summarising a span instead of giving it up), which will be a
    new class rather than a change to any caller."""
    return _REDUCER
