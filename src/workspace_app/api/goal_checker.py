"""#613 P3: the turn-end goal check — one cheap, blocking LLM call.

`check_goal_met` asks the checker model whether the chat's goal condition
holds, given the tail of the conversation, and parses a strict MET / NOT_MET
verdict. Blocking on purpose (`ILlm` streams under the hood — the "always
stream" rule is satisfied inside `collect`); the driver runs it off the loop
via `asyncio.to_thread`, like `promote`'s LLM call.

Ambiguous output counts as NOT met: the failure mode of a false "met" is a
silently abandoned goal, while a false "not met" merely spends a bounded round
— the hard `goal.max_rounds` cap is what contains an unreliable verdict.
"""

from __future__ import annotations

from ..kb.llm import ILlm
from ..resources.conversation import Message

# How much conversation tail the checker sees. Generous enough for the last
# exchange plus tool chatter; small enough to stay a cheap call.
_TAIL_MESSAGES = 8
_TAIL_CHARS = 6_000

_PROMPT = """\
You are a completion checker. A user set this goal for an ongoing AI work session:

GOAL: {condition}

Below is the tail of the session transcript (most recent last). Judge whether \
the GOAL is now fully achieved. Only what the transcript shows counts — \
claimed intentions or plans are not completion. Auto-continue prompts in the \
transcript (lines starting with "[goal]") are the driver re-asking; they are \
not evidence either way.

TRANSCRIPT:
{transcript}

Answer with exactly one word on the last line: MET if the goal is fully \
achieved, NOT_MET otherwise."""


def transcript_tail(messages: list[Message]) -> str:
    """The checker's view of the conversation: the last few user/assistant/tool
    messages, oldest first, clipped to a budget."""
    shown = [m for m in messages if m.role in ("user", "assistant", "tool")][-_TAIL_MESSAGES:]
    lines = [f"{m.role}: {m.content}" for m in shown if m.content]
    return "\n".join(lines)[-_TAIL_CHARS:]


def check_goal_met(llm: ILlm, condition: str, transcript: str) -> bool:
    """True iff the checker's verdict is MET (see the module docstring for why
    anything else — including garbage — reads as NOT met)."""
    out = llm.collect(
        _PROMPT.format(condition=condition, transcript=transcript),
        recover_reasoning=True,
    )
    for line in reversed(out.strip().splitlines()):
        token = line.strip().upper()
        if not token:
            continue
        if "NOT_MET" in token:
            return False
        if "MET" in token:
            return True
        break  # the last non-empty line carried no verdict → not met
    return False
