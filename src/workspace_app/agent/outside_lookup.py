"""`ask_outside` — the AI asks the person to look something up outside
(docs/plan-outside-lookup.md).

The backend may not reach the internet; the person's browser can. The model
passes either a `query` (the card offers one button per search destination,
`server.lookup_targets`) or a `url` (one button that opens it); the reply ends
with a declaration the chat draws as the "請幫我查" card. The turn stops when
the card is drawn, like `ask_user`; a refusal declares nothing and does not
stop it, so the model reads why and corrects the call. The person's answer —
what they pasted (saved as a file when they may add files), or "not found" —
is their next message.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from agents import FunctionTool, RunContextWrapper, function_tool

from .context import AgentToolContext

if TYPE_CHECKING:
    from ..resources.agent_config import AgentConfig

#: Ends an `ask_outside` reply: the card the chat draws. Mirrored by
#: `web/src/renderers/outsideLookup.ts`.
OUTSIDE_LOOKUP_MARKER = "\n[outside-lookup]"

TOOL_NAME = "ask_outside"


async def ask_outside_impl(
    ctx: RunContextWrapper[AgentToolContext],
    why: str,
    query: str | None = None,
    url: str | None = None,
) -> str:
    """Ask the user to look something up outside for you, with a card in the chat.

    The server you run on may not reach the internet; the user's browser can.
    Use this when answering needs public outside information you do not have — a
    library's current documentation, a release note, an error message others
    have reported, a page a document links to. To have the user choose between
    options instead, use `ask_user`.

    Pass `why` as one short sentence the user reads first, then exactly one of:
    - `query`: what to search for. The card offers search buttons; the user may
      edit the query before searching.
    - `url`: a web page (http or https) to open, such as a link you found in a
      file.

    One card asks for one thing. Your turn ends here. The user's answer is their
    next message: either what they found (usually also saved under `lookups/`;
    the message names the file when it is), or that they did not find it or
    chose not to look — then carry on with what you have and say the answer has
    no outside source."""
    why = why.strip()
    query = (query or "").strip() or None
    url = (url or "").strip() or None
    if not why:
        return "error: give `why` — one sentence the user reads before looking anything up."
    if (query is None) == (url is None):
        return "error: pass exactly one of `query` or `url` — one card asks for one thing."
    if url is not None and not _is_web_address(url):
        return (
            "error: `url` must be a web address starting with http:// or https://. "
            "To have the user search for something instead, pass `query`."
        )
    card = {"why": why, "query": query} if query is not None else {"why": why, "url": url}
    shown = query if query is not None else url
    said = (
        f"The user now sees a card asking them to look up {shown!r} outside. Wait for "
        "them: what they found, or that they could not find it, arrives as their next "
        "message."
    )
    return f"{said}{OUTSIDE_LOOKUP_MARKER}{json.dumps(card, ensure_ascii=False)}"


#: http(s), a host, no whitespace anywhere — the only kind of link the card
#: will open (D5): `javascript:`, `file:` and the like are not "a page on the
#: internet". A pattern rather than `urlsplit`, because the chat applies the
#: SAME one (`outsideLookup.ts`) and two URL parsers disagree (`http://a b` has
#: a host to one and throws in the other) — a card the turn stopped for that
#: the chat then cannot draw. And no `\s` either: Python's and JavaScript's
#: differ (U+FEFF is whitespace to one, U+0085 to the other), so the characters
#: are spelled out, identically in both. Held together by
#: `outside_lookup_cases.json`.
_SPACE = (
    "\\x00-\\x20\\x7f-\\x9f\\u00a0\\u1680\\u2000-\\u200b\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"
)
_WEB_ADDRESS = re.compile(f"https?://[^/?#{_SPACE}]+(?:[/?#][^{_SPACE}]*)?")


def _is_web_address(url: str) -> bool:
    return _WEB_ADDRESS.fullmatch(url) is not None


def declared_lookup(text: str) -> dict | None:
    """The card a reply declares, read the way the chat reads it
    (`web/src/renderers/outsideLookup.ts`): the tail after the LAST marker is
    one JSON object with a `why` and exactly one of a `query` or an http(s)
    `url`. `None` for anything else — a refusal quoting the marker included."""
    at = text.rfind(OUTSIDE_LOOKUP_MARKER)
    if at < 0:
        return None
    try:
        card = json.loads(text[at + len(OUTSIDE_LOOKUP_MARKER) :])
    except ValueError:
        return None
    if not isinstance(card, dict) or set(card) not in ({"why", "query"}, {"why", "url"}):
        return None
    if not all(isinstance(v, str) and v for v in card.values()):
        return None
    if "url" in card and not _is_web_address(card["url"]):
        return None
    return card


def ask_outside_granted(config: AgentConfig) -> bool:
    """Whether a turn holds `ask_outside` — where `ask_user` goes (D6): only
    a turn with a person in it to press the buttons."""
    from .tools import granted_builtin_names

    return "ask_user" in granted_builtin_names(config.allowed_tools)


def ask_outside_tool() -> FunctionTool:
    """Built apart from `build_tools`' table: granted by a rule
    (`litellm_runner._agent_for`), never by naming it in a config."""
    return function_tool(ask_outside_impl, name_override=TOOL_NAME)
