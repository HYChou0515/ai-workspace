"""`request_env` — the AI asks the user for a missing environment variable
with a card in the chat (docs/plan-env-request-card.md).

A package tool that cannot work without a credential exits 3 and names the
variable in its output. The model passes that name here; the reply ends with
a declaration the chat draws as a card — one button per name, "sign in" when
a login method produces it and "set" otherwise (the FE decides, from the
viewer's own values). The turn stops when the card is drawn, like `ask_user`;
a refusal does not stop it, so the model reads why and carries on.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agents import FunctionTool, RunContextWrapper, function_tool

from .context import AgentToolContext

if TYPE_CHECKING:
    from ..resources.agent_config import AgentConfig
    from ..tooling.registry import PackageInfo

#: Ends a `request_env` reply: the card the chat draws. Mirrored by
#: `web/src/renderers/envRequest.ts`.
ENV_REQUEST_MARKER = "\n[env-request]"

TOOL_NAME = "request_env"

#: A variable a tool can read from its environment and the panel can store.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


async def request_env_impl(
    ctx: RunContextWrapper[AgentToolContext],
    tool: str,
    names: list[str],
    reason: str,
) -> str:
    """Ask the user to sign in or set environment variables a package tool needs,
    with a card in the chat.

    Use it when one of your package tools failed because a variable is missing
    — it exited 3, or said a credential, token or key is not set — and its
    output names the variable. Pass `tool` exactly as you called it, `names` as
    the tool printed them, and `reason` as one short sentence the user reads
    above the buttons ("The ERP lookup needs you to sign in").

    The card shows one button per name: a sign-in when this deployment has a
    login for it, otherwise a field to fill in. Your turn ends here. When the
    user has set them they press Retry, which arrives as their next message —
    then run the tool again."""
    from ..tooling.registry import find_allowed_command, model_tool_name

    actx = ctx.context
    cfg = actx.agent_config
    try:
        found = find_allowed_command(actx.packages, cfg.allowed_tools if cfg else [], tool)
    except ValueError as exc:
        return f"error: {exc}"
    if found is None:
        return _not_a_package_tool(tool)
    if not names:
        return "error: name at least one variable the tool needs."
    if bad := [n for n in names if not _ENV_NAME.fullmatch(n)]:
        return f"error: {', '.join(map(repr, bad))} cannot be an environment variable name."
    pkg, cmd = found
    # The name the run was recorded under, whichever spelling `tool` used (the
    # grant's `pkg:cmd`, a third-party command's old bare name): the card and
    # the check both speak of the tool as the model holds it.
    tool = model_tool_name(pkg, cmd)
    declared = {n.name for n in pkg.env_needs or ()}
    said = actx.tool_outputs.get(tool, "")
    if unseen := [n for n in names if n not in declared and not _mentions(said, n)]:
        return (
            f"error: {', '.join(unseen)} does not appear in what `{tool}` last printed in "
            "this turn, and the tool does not declare it. Ask only for names the tool "
            "named — run it first if you have not."
        )
    card = {"tool": tool, "names": list(dict.fromkeys(names)), "reason": reason}
    said = (
        f"The user now sees a card asking for {', '.join(names)}. Wait for them: they "
        "will tell you when it is set, and then you run the tool again."
    )
    return f"{said}{ENV_REQUEST_MARKER}{json.dumps(card)}"


def declared_card(text: str) -> dict | None:
    """The card a reply declares, read the way the chat reads it
    (`web/src/renderers/envRequest.ts`): the tail after the LAST marker is one
    JSON object naming a tool, a non-empty list of names and a reason. `None`
    for anything else — a refusal that merely quotes the marker included."""
    at = text.rfind(ENV_REQUEST_MARKER)
    if at < 0:
        return None
    try:
        card = json.loads(text[at + len(ENV_REQUEST_MARKER) :])
    except ValueError:
        return None
    if not isinstance(card, dict):
        return None
    names = card.get("names")
    ok = (
        isinstance(card.get("tool"), str)
        and isinstance(card.get("reason"), str)
        and isinstance(names, list)
        and bool(names)
        and all(isinstance(n, str) and n for n in names)
    )
    return card if ok else None


def _mentions(text: str, name: str) -> bool:
    """`name` as a whole word: `ERP_TOKEN` is not mentioned by `ERP_TOKEN_V2`."""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


def _not_a_package_tool(tool: str) -> str:
    from .tools import _IMPLS

    if tool in _IMPLS:
        # N2: the item's variables are set on the exec that dispatches a
        # PACKAGE tool and nowhere else (`registry._exec_tool`).
        return (
            f"error: `{tool}` does not receive this item's environment variables, so "
            "setting one would change nothing. Only a package tool does — tell the "
            "user in your reply what the command needs instead."
        )
    return (
        f"error: `{tool}` is not a package tool you hold in this turn. Name the tool "
        "that failed, exactly as you called it."
    )


def request_env_granted(config: AgentConfig, packages: Sequence[PackageInfo]) -> bool:
    """Whether a turn holds `request_env` — the ONE rule, read by the turn's
    tool list (`litellm_runner._agent_for`) and by the exit-3 hint alike.

    The card asks a person to act, so it goes where `ask_user` goes (D6), and
    only when the turn holds at least one package tool: only a package tool is
    handed the item's variables — `exec` gets none."""
    from ..tooling.registry import allowed_command_names
    from .tools import granted_builtin_names

    allowed = config.allowed_tools
    return "ask_user" in granted_builtin_names(allowed) and bool(
        allowed_command_names(packages, allowed)
    )


def request_env_tool() -> FunctionTool:
    """The tool, built apart from `build_tools`' table: it is granted by a rule
    (`litellm_runner._agent_for`), never by naming it in a config."""
    return function_tool(request_env_impl, name_override=TOOL_NAME)
