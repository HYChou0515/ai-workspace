"""What an ``agent.ai.yaml`` says (`docs/plan-wui-publish-and-agent-hub.md` A4/A5).

One file defines a published agent: its name and look, its ``instructions:``,
the tools it narrows to, opening suggestions, and its publish settings. It is
to an agent what ``app.json`` plus a profile is to an App — except that it can
only NARROW within the App (A4): ``tools:`` is intersected with what the item
holds at the turn (`api/turn_context.py`), so a name it lists that the App does
not grant is simply not there. It cannot name a model, a preset or a new
external tool; there are no keys for them.

Its skills and workflows are the workspace root's plus its own folder's, its
own winning on a clash (A5). That is made true in the COPY a person talks to:
`overlay_destination` says where each of the folder's files lands, and the
copy is written with them over the root's (`api/publish_copy.py`), so every
reader of ``.skill/`` and ``.workflows/`` — the skill index, ``read_skill``,
the picker, the workflow runner and its schedules — sees one ordinary
workspace without knowing agents exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from .skills import WORKSPACE_SKILL_DIR

#: The view file that defines an agent — any folder may hold one (A4).
AGENT_FILE = "agent.ai.yaml"
#: The workflow definitions folder (`workflow/workspace_store.py`), spelled
#: here to keep this module free of the workflow package's imports.
_WORKFLOW_DIR = ".workflows"
_SCHEDULES = "schedules.json"

_KEYS = (
    "color",
    "description",
    "icon",
    "instructions",
    "publish",
    "suggestions",
    "title",
    "tools",
    "view",
)


@dataclass(frozen=True)
class AgentSpec:
    title: str
    instructions: str
    description: str = ""
    icon: str = ""
    color: str = ""
    #: ``None`` — the App's tools as the item holds them; a list — only these
    #: of them (``[]`` is an agent with no tools).
    tools: list[str] | None = None
    #: ``(label, prompt)`` chips a new conversation opens with.
    suggestions: list[tuple[str, str]] = field(default_factory=list)
    #: The publish dialog's pre-fill (W5/A4); it takes effect only when
    #: someone with C9 presses Publish, so it is carried, not applied.
    publish: dict[str, Any] = field(default_factory=dict)


def _text(doc: dict[str, Any], key: str, problems: list[str], *, required: bool = False) -> str:
    value = doc.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        problems.append(f"`{key}` must be text")
        return ""
    if required and not value.strip():
        problems.append(f"`{key}` is required — {_WHY[key]}")
    return value


_WHY = {
    "title": "it is the agent's name in Agent Hub",
    "instructions": "it is what makes this agent different from the App's own",
}


def _suggestions(raw: Any, problems: list[str]) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append("`suggestions` must be a list")
        return []
    out: list[tuple[str, str]] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append((entry, entry))
        elif (
            isinstance(entry, dict)
            and isinstance(entry.get("label"), str)
            and isinstance(entry.get("prompt"), str)
        ):
            out.append((entry["label"], entry["prompt"]))
        else:
            problems.append("`suggestions` entries are text, or `label:` and `prompt:` together")
            return []
    return out


def parse_agent_spec(text: str) -> tuple[AgentSpec | None, list[str]]:
    """The agent ``text`` defines and what is wrong with it.

    ``None`` when it does not define one (not yaml, not ``view: agent``, no
    title or instructions, a key of the wrong type). An unknown key is a
    problem but not a fatal one: the agent still runs, and the name is there
    to be shown — a typo like ``instruction:`` would otherwise be an agent
    silently running without what its author wrote."""
    try:
        loaded = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        return None, [f"not valid yaml: {exc}"]
    return spec_from_doc(loaded)


def spec_from_doc(loaded: Any) -> tuple[AgentSpec | None, list[str]]:
    """`parse_agent_spec` of a document already loaded (`api/publish.py` has it)."""
    if not isinstance(loaded, dict):
        return None, ["an agent.ai.yaml is a mapping of keys"]
    doc: dict[str, Any] = loaded
    fatal: list[str] = []
    if doc.get("view") != "agent":
        fatal.append("it must say `view: agent`")
    title = _text(doc, "title", fatal, required=True)
    instructions = _text(doc, "instructions", fatal, required=True)
    description = _text(doc, "description", fatal)
    icon = _text(doc, "icon", fatal)
    color = _text(doc, "color", fatal)
    tools: list[str] | None = None
    if "tools" in doc and doc["tools"] is not None:
        raw = doc["tools"]
        if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
            fatal.append("`tools` must be a list of tool names")
        else:
            tools = [str(t) for t in raw]
    suggestions = _suggestions(doc.get("suggestions"), fatal)
    publish = doc.get("publish") or {}
    if not isinstance(publish, dict):
        fatal.append("`publish` must be a mapping")
        publish = {}
    if fatal:
        return None, fatal
    warnings = [
        f"unknown key `{key}` — an agent.ai.yaml holds: {', '.join(_KEYS)}"
        for key in sorted(str(k) for k in doc)
        if key not in _KEYS
    ]
    return (
        AgentSpec(
            title=title,
            instructions=instructions,
            description=description,
            icon=icon,
            color=color,
            tools=tools,
            suggestions=suggestions,
            publish=publish,
        ),
        warnings,
    )


def agent_folder(path: str) -> str:
    """The folder an agent owns: its file's directory, ``""`` at the root."""
    cut = path.rfind("/")
    return "" if cut <= 0 else path[:cut]


def overlay_destination(path: str, folder: str) -> str | None:
    """Where ``path``, a file of the agent's ``folder``, also lands in a copy:
    ``<folder>/.skill/<name>/…`` → ``/.skill/<name>/…`` and a flat
    ``<folder>/.workflows/<id>.json`` → ``/.workflows/<id>.json`` (A5). ``None``
    for every other file, and for an agent at the root, whose folder IS the
    root's."""
    if not folder:
        return None
    prefix = f"{folder}/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix) :]
    skill = f"{WORKSPACE_SKILL_DIR}/"
    if rest.startswith(skill) and rest[len(skill) :].count("/") >= 1:
        return f"/{rest}"
    flows = f"{_WORKFLOW_DIR}/"
    if rest.startswith(flows):
        name = rest[len(flows) :]
        if "/" not in name and name.endswith(".json") and name != _SCHEDULES:
            return f"/{rest}"
    return None
