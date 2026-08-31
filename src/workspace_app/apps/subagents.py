"""Sub-agent definitions — `.agent/<name>/AGENT.md`.

A sub-agent is a named, narrowed agent the main agent can delegate a whole
sub-task to: its own system prompt (the file body), its own tool subset, and a
context that starts empty. The main agent gets back one report, not the noise
that produced it.

The format deliberately mirrors skills (`.skill/<name>/SKILL.md`, `apps/skills.py`)
— same YAML frontmatter, same dual source (an App profile ships some; the item's
workspace may add or override them), same tolerance for one bad hand-edit.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
from importlib import resources
from importlib.resources.abc import Traversable

import msgspec

from ..files import WorkspaceFiles
from .frontmatter import FrontmatterError, parse_frontmatter

logger = logging.getLogger(__name__)

_APPS_PKG = "workspace_app.apps"
_PROFILES_DIR = "profiles"
#: Where an App profile ships its own sub-agents — the package-side sibling of
#: `.skill/`. Never seeded into a workspace (read straight from the package).
PROFILE_AGENT_DIR = ".agent"

#: Where a user-authored sub-agent lives in a workspace — a sibling of `.skill/`.
WORKSPACE_AGENT_DIR = ".agent"

#: Hard cap on a body. The body IS the sub-agent's system prompt, so an
#: accidental paste of a log file would silently eat the turn's context window.
SUBAGENT_BODY_CAP = 50_000


class SubagentDef(msgspec.Struct, frozen=True):
    """One sub-agent the main agent may delegate to. `body` is its system prompt;
    `tools` is the set it may use (clamped against the App's ceiling before it
    ever reaches a turn)."""

    name: str
    description: str
    tools: list[str] = msgspec.field(default_factory=list)
    body: str = ""


async def load_subagents(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str,
    profile: str,
    *,
    ceiling: Collection[str] | None = None,
) -> list[SubagentDef]:
    """The sub-agents this turn may delegate to: what the App profile ships,
    overridden by name by what the item's workspace defines, all clamped to
    `ceiling`, sorted by name.

    One call rather than three, because every caller needs the same three steps
    and a caller that skipped the clamp would be a privilege-escalation hole."""
    merged = {d.name: d for d in profile_subagent_defs(app_slug, profile, ceiling=ceiling)}
    for d in await workspace_subagent_defs(files, workspace_id, ceiling=ceiling):
        merged[d.name] = d
    return [merged[name] for name in sorted(merged)]


def profile_subagent_defs(
    app_slug: str, profile: str, *, ceiling: Collection[str] | None = None
) -> list[SubagentDef]:
    """The sub-agents an App profile ships, sorted by name. Unknown profile / no
    `.agent/` dir → empty (a profile may ship none)."""
    root = _agent_root(app_slug, profile)
    if root is None:
        return []
    out: list[SubagentDef] = []
    for sub in sorted(root.iterdir(), key=lambda t: t.name):
        agent_md = sub / "AGENT.md"
        if not sub.is_dir() or not agent_md.is_file():
            continue
        defn = _def_from(agent_md.read_bytes(), sub.name)
        if defn is not None:
            out.append(clamp_tools(defn, ceiling))
    return out


def _agent_root(app_slug: str, profile: str) -> Traversable | None:
    try:
        pkg = resources.files(_APPS_PKG)
    except ModuleNotFoundError:  # pragma: no cover — a synthetic pkg in tests
        return None
    root = pkg / app_slug / _PROFILES_DIR / profile / PROFILE_AGENT_DIR
    try:
        if not root.is_dir():
            return None
    except (FileNotFoundError, NotADirectoryError):  # pragma: no cover — Traversable shim
        return None
    return root


async def workspace_subagent_defs(
    files: WorkspaceFiles, workspace_id: str, *, ceiling: Collection[str] | None = None
) -> list[SubagentDef]:
    """Every well-formed sub-agent under the workspace's `.agent/` dir, sorted by
    name. Read live (never cached) — the file IDE may have rewritten one since
    the last turn.

    `ceiling` is the App/profile tool ceiling: a definition file is user-authored,
    so the `tools:` it names is a REQUEST. Clamping here (rather than at the call
    site) is what keeps a hand-written file from granting itself `exec` on an App
    that has no sandbox."""
    prefix = f"/{WORKSPACE_AGENT_DIR}/"
    out: list[SubagentDef] = []
    for path in sorted(await files.ls(workspace_id, prefix)):
        rel = path[len(prefix) :]
        if rel.count("/") != 1 or not rel.endswith("/AGENT.md"):
            continue
        dir_name = rel[: -len("/AGENT.md")]
        defn = _def_from(await files.read(workspace_id, path), dir_name)
        if defn is not None:
            out.append(clamp_tools(defn, ceiling))
    return out


def subagents_block(defs: list[SubagentDef] | tuple[SubagentDef, ...]) -> str:
    """The per-turn "who you can delegate to" index, or `""` when there is
    nobody. Rendered fresh each turn, like the workspace skill index — a
    definition the user just wrote in the file IDE is callable on the next turn.

    Descriptions are the whole point: the model picks by "when would I use
    this", so a definition's `description` is written for the caller, not for
    the sub-agent itself."""
    if not defs:
        return ""
    lines = [
        "## Sub-agents you can delegate to",
        "",
        "Call `run_agent(agent_type, prompt)` to hand one a whole sub-task. It "
        "starts with an empty context and answers once, so the prompt must be "
        "self-contained.",
        "",
    ]
    lines += [f"- `{d.name}`: {d.description}" for d in defs]
    return "\n".join(lines)


def clamp_tools(defn: SubagentDef, ceiling: Collection[str] | None) -> SubagentDef:
    """`defn` with any tool outside `ceiling` dropped (logged). `None` ceiling ⇒
    unclamped, matching the tri-state `allowed_tools` convention."""
    if ceiling is None:
        return defn
    kept = [t for t in defn.tools if t in ceiling]
    if dropped := [t for t in defn.tools if t not in ceiling]:
        logger.warning("sub-agent %r: tools outside the ceiling, dropped: %s", defn.name, dropped)
    return msgspec.structs.replace(defn, tools=kept)


def _def_from(raw: bytes, dir_name: str) -> SubagentDef | None:
    """Parse one AGENT.md, or `None` (logged) when it's malformed — one bad
    hand-edit must not blank the whole index."""
    try:
        front, body = parse_frontmatter(raw)
    except FrontmatterError as e:
        logger.warning("sub-agent %r: %s — skipping", dir_name, e)
        return None
    name = str(front.get("name", "")).strip()
    if not name:
        logger.warning("sub-agent %r: missing `name` — skipping", dir_name)
        return None
    if name != dir_name:
        # The agent calls a sub-agent by the name it read in the index; if that
        # name doesn't lead back to this folder, the call can't be resolved.
        logger.warning(
            "sub-agent %r: frontmatter name=%r mismatches dir — skipping", dir_name, name
        )
        return None
    if len(body) > SUBAGENT_BODY_CAP:
        logger.warning(
            "sub-agent %r: body is %d chars (cap %d) — skipping",
            dir_name,
            len(body),
            SUBAGENT_BODY_CAP,
        )
        return None
    return SubagentDef(
        name=name,
        description=str(front.get("description", "")).strip(),
        tools=_parse_tools(front.get("tools")),
        body=body,
    )


def _parse_tools(value: object) -> list[str]:
    """`tools: [a, b]` — or a bare comma-separated `a, b`, since the shared
    frontmatter loader hands every value back as a string."""
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [t.strip() for t in text.split(",") if t.strip()]


def unrenderable_reason(
    slug: str, description: str, tools: Collection[str], body: str
) -> str | None:
    """`None` when `render_agent_md`'s output loads back as the SAME definition;
    otherwise a plain sentence saying what could not be stored.

    `save_subagent` promises that what it saves is always callable. Owning the
    format is not enough to keep that promise: the frontmatter parser is a
    line-based mini-YAML, so a `#` in the description truncates it, a value
    opening with `[` or `{` fails to parse, and a body one character under the
    cap crosses it once rendered. Each of those wrote a file the loader then
    skipped — the exact failure this tool exists to make unreachable.

    So the check is the round trip itself, not a blacklist of characters: render
    it, parse it back with the loader that will read it for real, and compare.
    A parser change can therefore never quietly reopen the hole."""
    rendered = render_agent_md(slug, description, tools, body)
    back = _def_from(rendered.encode("utf-8"), slug)
    if back is None:
        parsed_body = len(body.strip()) + 1  # render_agent_md ends the body with \n
        if parsed_body > SUBAGENT_BODY_CAP:
            return (
                f"the instructions come to {parsed_body} chars once saved, over the "
                f"{SUBAGENT_BODY_CAP} cap — it is a system prompt, not a document. State "
                "the method and point at files for the detail."
            )
        return (
            "the description could not be stored as written — a `#`, or an unclosed `[` "
            "or `{`, confuses the file format. Rephrase it in plain words."
        )
    if back.description != " ".join(description.split()):
        return (
            f"the description would be saved as {back.description!r}, not what you wrote — "
            "a `#` truncates it. Rephrase it without one."
        )
    if back.tools != [t.strip() for t in tools if t.strip()]:
        return f"the tool list would be saved as {back.tools!r}, not what you asked for."
    return None


def slugify_subagent_name(name: str) -> str:
    """A display name → kebab-case slug (lowercase; non-alphanumeric runs become
    a single ``-``; trimmed). `save_subagent` uses this so the frontmatter
    ``name`` always equals the folder name and `_def_from` never silently skips
    the file. ``""`` when nothing usable remains (the caller rejects)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def render_agent_md(slug: str, description: str, tools: Collection[str], body: str) -> str:
    """Assemble a well-formed AGENT.md: `name` + `description` + `tools`
    frontmatter, then the body.

    The inverse of `_parse_tools` above, and deliberately next to it: the two
    halves of one format drift the moment they live apart. ``description`` is
    collapsed to a single line because the frontmatter parser is line-based — a
    newline would truncate it."""
    desc = " ".join(description.split())
    listed = ", ".join(t.strip() for t in tools if t.strip())
    return f"---\nname: {slug}\ndescription: {desc}\ntools: [{listed}]\n---\n\n{body.strip()}\n"
