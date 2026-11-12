"""`ask_outside` — the AI asks the person to look something up outside
(docs/plan-outside-lookup.md).

The backend may not reach the internet; the person's browser can. The model
passes either a `query` (the card offers one button per search destination,
`server.lookup_targets`) or a page to open — a `url`, or a `file` + `link`
naming one of the links a saved lookup lists (docs/plan-outside-helper.md
D24) — drawn as one button that opens it; the reply ends
with a declaration the chat draws as the "請幫我查" card. The turn stops when
the card is drawn, like `ask_user`; a refusal declares nothing and does not
stop it, so the model reads why and corrects the call. The person's answer —
what they pasted (saved as a file when they may add files), or "not found" —
is their next message.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agents import FunctionTool, RunContextWrapper, function_tool

from ..config.schema import DEFAULT_QUERY_MAX_CJK, DEFAULT_QUERY_MAX_WORDS
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
    file: str | None = None,
    link: int | None = None,
    target: str | None = None,
) -> str:
    """Ask the user to look something up outside for you, with a card in the chat.

    The server you run on may not reach the internet; the user's browser can.
    Use this when answering needs public outside information you do not have — a
    library's current documentation, a release note, an error message others
    have reported, a page a document links to. To have the user choose between
    options instead, use `ask_user`.

    Pass `why` as one short sentence the user reads first, then exactly one of:
    - `query`: the keywords you suggest searching for — keywords, not a
      sentence, unless the target takes more: each search target listed below
      says how long its search may be (with no list: at most 10 words and at
      most 40 Chinese, Japanese or Korean characters). The card shows them
      beside an empty search box; the user types or pastes what is actually
      searched.
    - `file` and `link` together: a link a lookup brought back. A lookup saved
      as a file lists its links at the bottom, numbered `- [n] <https://…>`
      (the message that brought it back names the file and lists the first
      of them too).
      Pass the file's path and the number `n` — search results included —
      rather than copying the address: long addresses copied by hand come out
      wrong. A search brings back a results page: titles and snippets, not the
      pages. Open the results that answer the question this way and read them
      before answering, one card at a time.
    - `url`: any other web page (http or https), such as a link in another
      file, or in an answer that was not saved as a file.

    With `query`, you may also pass `target`: the name of the search target
    you suggest, from the list below. The user sees your suggestion and picks
    the target themselves.

    One card asks for one thing. Your turn ends here. The user's answer is their
    next message: either what they found (usually also saved under `lookups/`;
    the message names the file when it is), or that they did not find it or
    chose not to look — then carry on with what you have and say the answer has
    no outside source."""
    why = why.strip()
    query = (query or "").strip() or None
    url = (url or "").strip() or None
    file = (file or "").strip() or None
    if not why:
        return "error: give `why` — one sentence the user reads before looking anything up."
    if (file is None) != (link is None):
        return (
            "error: pass `file` and `link` together — the saved lookup and the number of "
            "one of the links listed at its bottom."
        )
    if sum(x is not None for x in (query, url, file)) != 1:
        return (
            "error: pass exactly one of `query` or `url` (or `file` with `link`) — one "
            "card asks for one thing."
        )
    if file is not None and link is not None:
        listed = await _listed_link(ctx, file, link)
        if listed.startswith("error:"):
            return listed
        url = listed
    targets = ctx.context.search_targets
    # docs/plan-remove-ext-ai.md R3: held to the target the search will meet —
    # the one suggested, else the first, which the card selects.
    target = (target or "").strip()
    meets = next((t for t in targets if t["name"] == target), targets[0] if targets else None)
    if query is not None and (too_long := _too_long(query, meets)):
        where = f"a search on {meets['name']}" if meets else "a search"
        return (
            f"error: {where} is {_limits(meets)}; this one has {too_long}. Shorten it to "
            "the keywords that matter, or suggest a target that takes it, and call again."
        )
    if url is not None and not is_web_address(url):
        return (
            "error: `url` must be a web address starting with http:// or https://. "
            "To have the user search for something instead, pass `query`."
        )
    card = {"why": why, "query": query} if query is not None else {"why": why, "url": url}
    # docs/plan-lookup-target-choice.md T8: only a target the deploy lists is a
    # suggestion — any other name is dropped, not refused: the search stands.
    if query is not None and target in {t["name"] for t in targets}:
        card["target"] = target
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


#: Chinese, Japanese and Korean characters — the second ruler of a target's
#: query length (`config.schema.DEFAULT_QUERY_MAX_CJK`; Korean, spaced, meets
#: both). Only the AI's query is held to it: the person may edit it.
_CJK_CHARACTER = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def _rulers(target: Mapping[str, Any] | None) -> tuple[int, int]:
    """`(max_words, max_cjk)` of the target a search meets; with no target
    listed at all (a context built without the deploy's), today's rule."""
    if target is None:
        return DEFAULT_QUERY_MAX_WORDS, DEFAULT_QUERY_MAX_CJK
    return target["max_words"], target["max_cjk"]


def _limits(target: Mapping[str, Any] | None) -> str:
    """The target's query length in words — `0` lifts that ruler."""
    words, cjk = _rulers(target)
    said = [
        f"{words} word{'s' if words != 1 else ''}" if words else "",
        f"{cjk} Chinese, Japanese or Korean character{'s' if cjk != 1 else ''}" if cjk else "",
    ]
    said = [s for s in said if s]
    return "at most " + " and ".join(said) if said else "any length"


def _too_long(query: str, target: Mapping[str, Any] | None) -> str:
    """How `query` is too long for `target` — "" when it is not."""
    max_words, max_cjk = _rulers(target)
    words, cjk = len(query.split()), len(_CJK_CHARACTER.findall(query))
    if max_words and words > max_words:
        return f"{words} words"
    if max_cjk and cjk > max_cjk:
        return f"{cjk} characters"
    return ""


def is_web_address(url: str) -> bool:
    return _WEB_ADDRESS.fullmatch(url) is not None


#: Heads the numbered list of links under a saved lookup (D24): a heading
#: line people read too (an HTML comment showed as text in the file viewer).
LINK_LIST_MARKER = "## 查到的連結"
_LISTED = re.compile(r"- \[(\d+)\] <([^>\s]+)>")


def listed_links(text: str) -> dict[int, str]:
    """`{n: address}` of the list under a saved lookup; `{}` without one.
    What `link_list` writes and what `file` + `link` reads.

    The list is the last thing in the file, so it is read from the end: rows
    and blank lines, then this heading. Anything else ends it. So the answer
    above — the page as it came, with `[1]: …` lines, list-like rows, this
    heading, code it never closed — is never read as the list (reading code
    fences forwards lost the list to an answer ending in an open one)."""
    lines = text.split("\n")
    rows: dict[int, str] = {}
    at = len(lines) - 1
    while at >= 0:
        line = lines[at].strip()
        if line == LINK_LIST_MARKER:
            return rows
        m = _LISTED.match(line)
        if line and m is None:
            return {}
        if m is not None:
            rows.setdefault(int(m.group(1)), m.group(2))
        at -= 1
    return {}


async def _listed_link(ctx: RunContextWrapper[AgentToolContext], file: str, link: int) -> str:
    """The address `file`'s link list numbers `link`, or an `error:` saying why not."""
    from ..filestore.protocol import FileNotFound
    from .tool_authz import authorize_tool
    from .tools import _workspace, rel_path

    if (denied := authorize_tool(ctx.context, "read_content")) is not None:
        return denied
    fs, inv = _workspace(ctx)
    try:
        text = (await fs.read(inv, file)).decode("utf-8", errors="replace")
    except FileNotFound:
        return f"error: file not found: {rel_path(file)}"
    listed = listed_links(text)
    if link not in listed:
        have = f"its links are [1]–[{max(listed)}]" if listed else "it lists no links"
        return f"error: {rel_path(file)} has no link [{link}] — {have}. Pass `url` instead."
    return listed[link]


#: The kinds of card: a search, a page to open, a question for the external AI.
_CARD_KEYS = (
    {"why", "query"},
    # docs/plan-lookup-target-choice.md: the target the AI suggests, beside a search only
    {"why", "query", "target"},
    {"why", "url"},
)


def declared_lookup(text: str) -> dict | None:
    """The card a reply declares, read the way the chat reads it
    (`web/src/renderers/outsideLookup.ts`): the tail after the LAST marker is
    one JSON object with a `why` and exactly one of a `query` (with, maybe, the
    `target` the AI suggests — docs/plan-lookup-target-choice.md) or an http(s)
    `url`. `None` for anything else — a refusal quoting the marker included."""
    at = text.rfind(OUTSIDE_LOOKUP_MARKER)
    if at < 0:
        return None
    try:
        card = json.loads(text[at + len(OUTSIDE_LOOKUP_MARKER) :])
    except ValueError:
        return None
    if not isinstance(card, dict) or set(card) not in _CARD_KEYS:
        return None
    if not all(isinstance(v, str) and v for v in card.values()):
        return None
    if "url" in card and not is_web_address(card["url"]):
        return None
    return card


def ask_outside_granted(config: AgentConfig) -> bool:
    """Whether a turn holds `ask_outside` — where `ask_user` goes (D6): only
    a turn with a person in it to press the buttons."""
    from .tools import granted_builtin_names

    return "ask_user" in granted_builtin_names(config.allowed_tools)


def ask_outside_tool(targets: Sequence[Mapping[str, Any]] = ()) -> FunctionTool:
    """Built apart from `build_tools`' table: granted by a rule
    (`litellm_runner._agent_for`), never by naming it in a config.

    `targets` are the deploy's search targets (docs/plan-lookup-target-choice.md
    T3): listed in the description, each with the text the person reads too,
    so the AI suggests one knowing what it searches and shapes its keywords
    for it. Fixed at boot, so the description is too."""
    tool = function_tool(ask_outside_impl, name_override=TOOL_NAME)
    if targets:
        rows = [
            f"- {t['name']} ({_limits(t)})" + (f": {t['description']}" if t["description"] else "")
            for t in targets
        ]
        tool.description += "\n\nSearch targets (`target`):\n" + "\n".join(rows)
    return tool
