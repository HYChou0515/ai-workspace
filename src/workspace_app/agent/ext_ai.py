"""`ask_ext_ai` — the AI asks the company's web AI, through the person's helper
(docs/plan-ext-ai.md).

The company has an AI that reaches the internet and has only a web page; the
backend reaches neither. The model drafts a question; the chat draws it as a
third kind of "請幫我查" card (`{why, question}`, the same declaration as
`ask_outside`), where the person reads and edits it and presses 「問外部AI」 —
their helper then asks it in their Chrome and brings the answer back (or they
ask it themselves and paste the answer). The turn stops at the card, like
`ask_user`; a refusal declares nothing and does not stop it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agents import FunctionTool, RunContextWrapper, function_tool

from .context import AgentToolContext
from .outside_lookup import OUTSIDE_LOOKUP_MARKER, ask_outside_granted

if TYPE_CHECKING:
    from ..resources.agent_config import AgentConfig

TOOL_NAME = "ask_ext_ai"


async def ask_ext_ai_impl(
    ctx: RunContextWrapper[AgentToolContext],
    why: str,
    question: str,
) -> str:
    """Ask the external AI a question, with a card in the chat.

    The external AI reaches the internet. Use it for someone else's analysis:
    compare approaches, explain an error, summarise the current state of a
    topic. Its answer is the external AI's account: when you use it, say it
    comes from the external AI. For an original page — documentation, a
    release note, search results — use `ask_outside`.

    Pass `why` as one short sentence the user reads first, and `question` as
    text with everything it needs written into it: each question opens a new
    conversation with the external AI, so include the background, the data and
    the code it should look at. The card shows the question, and
    the user may edit it before it is sent.

    One card asks one question. Your turn ends here. The external AI's answer
    is the user's next message (usually also saved under `lookups/`; the message
    names the file when it is), or that they did not ask — then carry on with
    what you have."""
    why = why.strip()
    question = question.strip()
    if not why:
        return "error: give `why` — one sentence the user reads before anything is sent."
    if not question:
        return "error: give `question` — the whole question, as text, for the external AI."
    card = {"why": why, "question": question}
    said = (
        "The user now sees a card asking the external AI your question. Wait for them: "
        "its answer, or that they did not ask, arrives as their next message."
    )
    return f"{said}{OUTSIDE_LOOKUP_MARKER}{json.dumps(card, ensure_ascii=False)}"


def ask_ext_ai_granted(config: AgentConfig, *, enabled: bool) -> bool:
    """Whether a chat turn holds `ask_ext_ai`: where `ask_outside` goes (a
    person to press the button), on a deploy that turned it on (D5)."""
    return enabled and ask_outside_granted(config)


def ask_ext_ai_tool() -> FunctionTool:
    """Built apart from `build_tools`' table: granted by a rule
    (`litellm_runner._agent_for`), never by naming it in a config."""
    return function_tool(ask_ext_ai_impl, name_override=TOOL_NAME)
