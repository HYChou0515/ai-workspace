"""Host-side registry: read prebuilt tool bundles + build LLM-facing
FunctionTools that exec into the sandbox.

The prebuilt layout (built by ``workspace_app.tooling.prebuild``) is
the source of truth:

    PREBUILT_DIR/
      <package>/
        commands.json        [{"name", "description"}, ...]
        schemas/<cmd>.json   {"name", "description", "params_json_schema"}
        launch               (sandbox-side binary; not read at host)
        ...

``discover_packages`` walks that tree and returns ``PackageInfo`` list;
``build_function_tools`` expands ``allowed_tools`` (the colon syntax
``"pkg"`` / ``"pkg:cmd"``) into one ``FunctionTool`` per selected
command. A first-party command keeps its flat name and a collision
between two first-party packages raises, so the deployer gets a clear
signal rather than opaque LLM confusion; a third-party command is named
``<local name>__<command>`` and cannot collide
(docs/plan-third-party-tool-names.md).

See `docs/plan-skills-and-tools.md` §B.3.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext

from ..agent.context import AgentToolContext
from ..agent.output_cap import cap_tool_outputs
from ..agent.plot_review import run_review
from ..agent.shown_files import declare_shown_files, describe_for_display
from ..agent.tools import _exec_result_text
from ..sandbox.protocol import ExecResult, SandboxHandle, SandboxNotFound
from .artifact import parse_env_declaration

logger = logging.getLogger(__name__)

#: `(the tool's ToolContext, the model's name for the command) -> None` — what
#: a catalog entry calls so the rest of the turn sends the real schema. A
#: callback rather than a direct write so `registry` stays free of the turn
#: machinery (and so the entry is testable without a turn). The whole
#: `ToolContext` goes in, not just `.context`: the caller also needs the call
#: id, to keep this call's answer (a schema) out of the stored thread.
#:
#: Spelled out rather than `Callable[[Any, str], None]`: with `Any` here, both
#: members the only implementation touches (`.context.mark_tool_loaded` and
#: `.tool_call_id`) could be renamed and `ty` stayed green — the review
#: renamed both and measured it (round 1 C3, the
#: `reference_kwargs_dict_loses_type_check` class).
MarkLoaded = Callable[[ToolContext[AgentToolContext], str], None]


@dataclass(frozen=True)
class CommandInfo:
    """One command within a package: what the LLM sees + what we exec.

    ``params_json_schema`` is the pydantic-derived JSON schema cached
    from ``launch <cmd>`` at prebuild time — we pass it verbatim to the
    LLM as the FunctionTool's schema."""

    name: str
    description: str
    params_json_schema: dict[str, Any]


@dataclass(frozen=True)
class EnvNeed:
    """One environment variable a package says it wants (#750).

    Hand-written by the package's author into ``env.json`` and copied
    verbatim into the bundle. ``name`` is the only required key: the point is
    to make a diligent author's package nicer to configure without making a
    lazy author's package worse than it is today.

    NOTHING enforces this. It is not a filter, a gate, or a promise — a tool
    still receives every variable the item holds, and a name absent from here
    is not refused. It exists so a person filling the panel can stop guessing.
    """

    name: str
    description: str = ""
    required: bool | None = None
    """``None`` means the author did not say — which is neither "required" nor
    "optional". Defaulting it either way would invent a fact: assume required
    and an undecided author's package reports permanently-missing variables
    (and a panel that always complains is one nobody reads); assume optional
    and a genuinely needed variable is presented as safe to skip."""


@dataclass(frozen=True)
class PackageInfo:
    """One prebuilt package the sandbox can run. ``install_dir`` is the
    *sandbox-relative* path the launch binary lives at — the sandbox
    bind-mounts ``PREBUILT_DIR/<name>`` at ``/.tools/<name>`` (reached
    as ``../.tools/<name>`` from the workspace at ``/root``)."""

    name: str
    commands: tuple[CommandInfo, ...]
    install_dir: str
    env_needs: tuple[EnvNeed, ...] | None = None
    """What this package says it wants from the environment (#750), or
    ``None`` when it shipped no ``env.json``.

    ``None`` and ``()`` are DIFFERENT and must stay so all the way to the UI:
    ``()`` is "the author looked and it needs nothing", ``None`` is "nobody
    said". Every package predating #750 is ``None``, so collapsing the two
    would let the panel state, about almost every tool installed, that it
    needs no configuration — the one sentence it is least entitled to say."""

    description: str = ""
    """What this tool IS, in its author's own words, or ``""`` when they said
    nothing.

    A bundle could previously only be described by listing the commands it
    ships, which is an inventory rather than a purpose — and it is the platform
    talking, not the author. Optional on purpose: a tool that says nothing is
    exactly as usable as it was, and every artifact published before the field
    existed says nothing."""

    version: str = ""
    """The release this package resolved at, or ``""`` for a first-party
    package — it has no artifact and no release."""

    author: str = ""
    """Who published it, as a display string, or ``""`` when nobody said: a
    first-party package, or an artifact built before the builder wrote the
    field. Never "unknown" — that would be the platform putting words in an
    author's mouth."""

    third_party: bool = False
    """A tool published from outside this repo (#674), named by the operator's
    local name in `app.json` `external_tools`. Its commands reach the model as
    `<name>__<command>` (docs/plan-third-party-tool-names.md): two authors who
    never see each other's command names cannot be asked to keep them apart."""

    latest_version: str | None = None
    """plan-tool-running-version: set only when the live sandbox runs a
    DIFFERENT bundle from the latest release (compared by sha — which says
    "different", not "older"). ``version`` is then the mounted release (``""``
    when it was not recorded) and this is the latest (``""`` likewise). ``None``
    whenever they are the same, so the description gains nothing then."""


def _read_env_needs(pkg_dir: Path) -> tuple[EnvNeed, ...] | None:
    """This package's `env.json`, or ``None`` when it shipped none (#750).

    Absent is NOT empty — see ``PackageInfo.env_needs``. The file is optional
    precisely because every package that predates #750 lacks it, and a missing
    hint must never be worse than the no-hint status quo.

    WHAT the file means is decided once, by ``parse_env_declaration`` — the
    same function the two build paths use. Only WHAT TO DO ABOUT A BAD ONE
    differs here, and that difference is the whole point of this wrapper (see
    below). A second opinion on the parsing itself is what let a BOM'd file
    build green and then arrive here as silence: the builder had been taught
    to read one encoding and this had not (#763).

    The FILE is the unit: one unusable entry degrades the whole declaration,
    it does not get skipped. A list with the bad rows quietly dropped would
    reach the panel looking complete, and "here is what this tool needs",
    missing two names, is a worse answer than "this tool did not say" — the
    same reason absent and empty are kept apart at all."""
    env_json = pkg_dir / "env.json"
    if not env_json.is_file():
        return None
    try:
        return tuple(
            EnvNeed(name=e.name, description=e.description, required=e.required)
            for e in parse_env_declaration(env_json.read_text("utf-8-sig"))
        )
    except (OSError, ValueError):
        # Degrade to "nobody said" rather than raise — this is the ONLY thing
        # this reader does differently, and it is deliberate. `commands.json`
        # is strict because a missing command list means the tool cannot run;
        # a malformed hint means only that the hint is missing, and raising
        # here would let one third-party author's typo stop an operator's
        # startup for EVERY package — the convenience turning itself into an
        # outage. The author gets the loud failure at prebuild instead, on
        # their own build.
        logger.warning(
            "registry: package %r has an unreadable env.json; treating it as "
            "undeclared. The tool still runs; only its environment hint is lost.",
            pkg_dir.name,
            exc_info=True,
        )
        return None


def discover_packages(prebuilt_dir: Path) -> list[PackageInfo]:
    """Walk ``prebuilt_dir`` and return one ``PackageInfo`` per subdir.

    **Strict**: every top-level subdir MUST be a complete bundle —
    ``commands.json`` AND ``schemas/`` AND every command listed in
    ``commands.json`` must have its ``schemas/<name>.json``. Anything
    half-built raises ``RuntimeError`` naming the offender; a missing
    ``prebuilt_dir`` itself raises ``FileNotFoundError``.

    The old silent-skip semantics caused a real production-style
    incident (May-30): three packages were half-built (``commands.json``
    + ``schemas/`` missing under each), discover returned ``[]``, and
    the agent ran with zero tool packages — the operator didn't know
    until the LLM "what tools do you have" reply listed only the base
    7. Fail-fast surfaces the same condition at startup instead of at
    LLM-reply time.

    Non-dir entries at the root (a ``README``, ``.DS_Store``) are still
    silently skipped — they're orthogonal content, not half-built
    bundles. Callers that genuinely don't want any packages should gate
    on their own ``PACKAGES`` registry being empty BEFORE calling, not
    rely on this function to silently no-op (see ``__main__.py``)."""
    if not prebuilt_dir.is_dir():
        raise FileNotFoundError(
            f"prebuilt tool dir not found at {prebuilt_dir}; "
            "run `uv run python scripts/prebuild_tools.py` (or set "
            "WORKSPACE_TOOLS_DIR to your prebuild output). If this deploy "
            "intentionally has no tool packages, clear "
            "`workspace_app.tooling.packages.PACKAGES` instead."
        )
    out: list[PackageInfo] = []
    for sub in sorted(prebuilt_dir.iterdir()):
        if not sub.is_dir():
            logger.debug("registry: skipping non-dir entry %s in %s", sub.name, prebuilt_dir)
            continue
        commands_json = sub / "commands.json"
        schemas_dir = sub / "schemas"
        if not commands_json.is_file():
            raise RuntimeError(
                f"package {sub.name!r} at {sub} is half-built: commands.json missing. "
                "Rerun `uv run python scripts/prebuild_tools.py` — this subdir was "
                "created by a prior prebuild that didn't finish, or by foreign "
                "content dropped into PREBUILT_DIR."
            )
        if not schemas_dir.is_dir():
            raise RuntimeError(
                f"package {sub.name!r} at {sub} is half-built: schemas/ dir missing "
                "(commands.json exists). Rerun `uv run python scripts/prebuild_tools.py`."
            )
        meta = json.loads(commands_json.read_text())
        commands: list[CommandInfo] = []
        for entry in sorted(meta, key=lambda m: m["name"]):
            schema_path = schemas_dir / f"{entry['name']}.json"
            if not schema_path.is_file():
                raise RuntimeError(
                    f"package {sub.name!r}: command {entry['name']!r} declared in "
                    f"commands.json but its schema file is missing at {schema_path}. "
                    "Rerun `uv run python scripts/prebuild_tools.py`."
                )
            schema_obj = json.loads(schema_path.read_text())
            commands.append(
                CommandInfo(
                    name=schema_obj["name"],
                    description=schema_obj["description"],
                    params_json_schema=schema_obj["params_json_schema"],
                )
            )
        out.append(
            PackageInfo(
                name=sub.name,
                commands=tuple(commands),
                install_dir=f"../.tools/{sub.name}",
                env_needs=_read_env_needs(sub),
            )
        )
    logger.info("registry: discovered %d tool package(s) from %s", len(out), prebuilt_dir)
    return out


def build_function_tools(
    packages: Sequence[PackageInfo],
    *,
    allowed: list[str] | None,
) -> list[FunctionTool]:
    """Expand ``allowed`` (colon syntax: ``"pkg"`` for the full package,
    ``"pkg:cmd"`` for one command) into FunctionTools.

    ``allowed=None`` → every command of every discovered package
    (the deploy didn't restrict, so expose everything). This
    matches ``build_tools(None)``'s "all workspace tools" semantics —
    the prior asymmetry (None ⇒ empty here) silently dropped package
    tools whenever an AgentConfig had ``allowed_tools=[]`` (default).
    ``allowed=[]`` is the explicit "no packages" caller intent.
    Unknown package names / command names are silently skipped —
    mirrors the legacy ``build_tools`` behaviour and keeps the LLM
    from seeing 500s for a config typo.

    Raises ``ValueError`` if two *different* first-party packages export
    the same command name within the same selection — the flat name would
    shadow one tool, so we want the deployer to rename one. Third-party
    commands carry their local name and never collide."""
    selected = _granted(packages, allowed)
    logger.debug("registry: %d function tool(s) selected (allowed=%s)", len(selected), allowed)
    # The same ceiling the built-ins get: a package command's stdout already
    # goes through `_exec_result_text`, but nothing structurally stops a future
    # command from answering with something bigger by another route.
    return cap_tool_outputs([_to_function_tool(pkg, cmd) for pkg, cmd in selected])


def third_party_aliases(
    packages: Sequence[PackageInfo],
    *,
    allowed: list[str] | None,
) -> dict[str, tuple[tuple[str, str], ...]]:
    """The names a granted third-party command used to be called by, each to
    the `(local name, model name)` of every command answering to it
    (docs/plan-third-party-tool-names.md N2): its bare command name and the
    grant spelling `<local name>:<command>`. Two entries for one bare name is
    a collision — the caller fails that call, never the turn.

    The SAME expansion as `build_function_tools` (D3): an old name cannot
    reach a command the turn was not granted."""
    if allowed is None:
        selected = [(pkg, cmd) for pkg in packages for cmd in pkg.commands]
    else:
        selected = _select_commands(packages, allowed)
    out: dict[str, list[tuple[str, str]]] = {}
    for pkg, cmd in _callable_by_name(selected):
        if not pkg.third_party:
            continue
        target = (pkg.name, model_tool_name(pkg, cmd))
        out.setdefault(cmd.name, []).append(target)
        out.setdefault(f"{pkg.name}:{cmd.name}", []).append(target)
    return {name: tuple(sorted(set(targets))) for name, targets in out.items()}


def allowed_command_names(
    packages: Sequence[PackageInfo],
    allowed: list[str] | None,
) -> list[str]:
    """Every package command this allow-list grants, by the name the model is
    given (`model_tool_name`: flat for first-party, `<local name>__<command>`
    for third-party).

    The model cannot tell one of these from a built-in by looking: `data-fetch`
    and `read_file` are both just names in its toolset. So anything that has to
    say WHICH of an agent's tools are package tools — a WUI's `tools:`
    declaration is the case this exists for — has to be told, and told from the
    same expansion the toolset itself was built from."""
    every = [p.name for p in packages]
    selected = _callable_by_name(
        _select_commands(packages, allowed if allowed is not None else every)
    )
    return sorted({model_tool_name(pkg, cmd) for pkg, cmd in selected})


def find_allowed_command(
    packages: Sequence[PackageInfo],
    allowed: list[str] | None,
    name: str,
) -> tuple[PackageInfo, CommandInfo] | None:
    """The package command called ``name``, if this allow-list grants it.

    Resolution goes through the SAME expansion and collision check the model's
    toolset is built from (`build_function_tools`), so a caller outside a turn
    cannot reach a command the agent in that item could not — including the
    `allowed=None` "the deploy did not restrict" case, which means everything
    there and has to mean everything here.

    Matched first on the name the model is given (`model_tool_name` — flat for
    a first-party command, `<local name>__<command>` for a third-party one),
    then on the grant spelling `<local name>:<command>`, then on a third-party
    command's bare name when only one granted command has it
    (docs/plan-third-party-tool-names.md N2); a bare name two have raises."""
    every = [p.name for p in packages]
    selected = _callable_by_name(
        _select_commands(packages, allowed if allowed is not None else every)
    )
    _check_collisions(selected)
    by_model = {model_tool_name(pkg, cmd): (pkg, cmd) for pkg, cmd in selected}
    if name in by_model:
        return by_model[name]
    # The grant spelling a page may also use: `<local name>:<command>`.
    if ":" in name:
        pkg_name, _, cmd_name = name.partition(":")
        return next(
            ((p, c) for p, c in selected if p.name == pkg_name and c.name == cmd_name), None
        )
    # An old bare name (written before the prefix): only a third-party one,
    # and never a built-in's — `read_file` means the built-in, as it does to
    # the model (D6).
    from ..agent.tools import builtin_tool_names

    if name in builtin_tool_names():
        return None
    matches = [(p, c) for p, c in selected if p.third_party and c.name == name]
    if len(matches) > 1:
        raise ValueError(
            ambiguous_name_message(name, [(p.name, model_tool_name(p, c)) for p, c in matches])
        )
    return matches[0] if matches else None


async def exec_package_command(
    actx: AgentToolContext,
    handle: SandboxHandle,
    pkg: PackageInfo,
    cmd_name: str,
    args_json: str,
    *,
    exec_timeout: float | None = None,
) -> ExecResult:
    """Run one package command. The public face of `_exec_tool`, so a caller
    outside a turn (a WUI's `callTool`) goes through the same launcher, the same
    item environment and the same exit-code contract as the model's call —
    "inject at the call site" was only one place until it was two.

    ``exec_timeout`` replaces the sandbox's own wall-clock cap for this one
    command — a page's `startTool` job (#942) runs to its own deadline, not
    the minute a call gets. None keeps the sandbox's."""
    return await _exec_tool(actx, handle, pkg, cmd_name, args_json, exec_timeout=exec_timeout)


def _granted(
    packages: Sequence[PackageInfo],
    allowed: list[str] | None,
) -> list[tuple[PackageInfo, CommandInfo]]:
    """The commands this allow-list grants, minus the ones a provider would
    refuse, with collisions rejected.

    ONE function because two builders read it: the executable tools and the
    catalog stubs that advertise them. A stub for a command the executable
    builder drops is a tool that can never run; a command the catalog omits is
    one the model never learns to ask for. Same code, so neither can happen.
    """
    selected = (
        [(pkg, cmd) for pkg in packages for cmd in pkg.commands]
        if allowed is None
        else _select_commands(packages, allowed)
    )
    selected = _callable_by_name(selected)
    _check_collisions(selected)
    return selected


def _select_commands(
    packages: Sequence[PackageInfo],
    allowed: Iterable[str],
) -> list[tuple[PackageInfo, CommandInfo]]:
    by_name = {p.name: p for p in packages}
    out: list[tuple[PackageInfo, CommandInfo]] = []
    for spec in allowed:
        if ":" in spec:
            pkg_name, _, cmd_name = spec.partition(":")
        else:
            pkg_name, cmd_name = spec, None
        pkg = by_name.get(pkg_name)
        if pkg is None:
            logger.debug("registry: unknown package %r in allowed, skipping", pkg_name)
            continue  # unknown package — silently skip
        if cmd_name is None:
            out.extend((pkg, c) for c in pkg.commands)
            continue
        cmd = next((c for c in pkg.commands if c.name == cmd_name), None)
        if cmd is None:
            logger.debug("registry: unknown command %r in package %r, skipping", cmd_name, pkg.name)
            continue  # unknown command — silently skip
        out.append((pkg, cmd))
    return out


#: What a model's tool name may be: providers accept letters, digits, `_` and
#: `-`, up to 64 (OpenAI's function-name rule; Anthropic's is the same).
_MODEL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: Between a third-party tool's local name and its command: providers refuse
#: `:`, so the grant syntax's separator cannot be the model's.
THIRD_PARTY_SEP = "__"
_warned_names: set[str] = set()


#: How `ambiguous_name_message` begins — what the usage count reads to tell
#: this refusal (nothing ran) from a reply (plan-tool-usage-stats §3.3).
COLLISION = "cross-package tool name collision:"


def ambiguous_name_message(called: str, targets: Sequence[tuple[str, str]]) -> str:
    """What a caller is told when an old name answers to two third-party
    commands (`targets` = `(local name, model name)`): today's collision
    sentence, then the names to call instead. ONE renderer, for the model's
    call and a page's alike (docs/plan-third-party-tool-names.md N2)."""
    packages = sorted({pkg for pkg, _name in targets})
    names = " or ".join(f"`{name}`" for _pkg, name in sorted(targets))
    return f"{COLLISION} command {called!r} appears in packages {packages} — call {names} instead"


def model_tool_name(pkg: PackageInfo, cmd: CommandInfo) -> str:
    """The name the model calls `cmd` by: `<local name>__<command>` for a
    third-party tool, the command itself for a first-party one."""
    return f"{pkg.name}{THIRD_PARTY_SEP}{cmd.name}" if pkg.third_party else cmd.name


def _callable_by_name(
    selected: list[tuple[PackageInfo, CommandInfo]],
) -> list[tuple[PackageInfo, CommandInfo]]:
    """`selected` minus the commands whose model name a provider would refuse
    (D1) — one such name in the list makes the provider reject the whole turn.
    Said once per name in the log, naming the tool and the command."""
    kept = []
    for pkg, cmd in selected:
        name = model_tool_name(pkg, cmd)
        # A local name holding the separator would let two commands meet in
        # one name (`a__b`'s `c`, `a`'s `b__c`).
        if _MODEL_NAME.fullmatch(name) and not (pkg.third_party and THIRD_PARTY_SEP in pkg.name):
            kept.append((pkg, cmd))
        elif name not in _warned_names:
            _warned_names.add(name)
            logger.warning(
                "registry: %s:%s is not offered to the model — its tool name %r has characters "
                "a model provider refuses, is over 64 long, or its tool's local name holds "
                "'__'; rename the tool or the command",
                pkg.name,
                cmd.name,
                name,
            )
    return kept


def _check_collisions(selected: list[tuple[PackageInfo, CommandInfo]]) -> None:
    """If two different packages export a command with the same name in
    the selected set, raise a clear ValueError listing both packages."""
    by_cmd: dict[str, list[str]] = {}
    for pkg, cmd in selected:
        # By the name the model sees: a third-party command carries its tool's
        # local name, so only first-party packages can still meet here (D2).
        by_cmd.setdefault(model_tool_name(pkg, cmd), []).append(pkg.name)
    collisions = {cmd_name: pkgs for cmd_name, pkgs in by_cmd.items() if len(set(pkgs)) > 1}
    if collisions:
        msg = "; ".join(
            f"command {cmd!r} appears in packages {sorted(set(pkgs))}"
            for cmd, pkgs in collisions.items()
        )
        raise ValueError(f"{COLLISION} {msg}")


def _accepts_style(schema: dict[str, Any]) -> bool:
    """True if the command's params schema exposes a ``style`` override (so a
    re-render can adjust layout) — the gate for the #285 VLM review loop."""
    if "style" in schema.get("properties", {}):
        return True
    return any(
        isinstance(d, dict) and "style" in d.get("properties", {})
        for d in schema.get("$defs", {}).values()
    )


def _images_from_stdout(stdout: bytes) -> list[str]:
    """Image paths a command reported on stdout: ``{"images": [...]}`` (sci-plot)
    or ``{"plots": [...]}`` (csv-column-summary). ``[]`` if stdout isn't that shape."""
    try:
        obj = json.loads(stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        logger.debug("registry: exec stdout not image-json, no image paths extracted")
        return []
    if not isinstance(obj, dict):
        return []
    for key in ("images", "plots"):
        v = obj.get(key)
        if isinstance(v, list):
            return [p for p in v if isinstance(p, str)]
    return []


async def _exec_tool(
    actx: AgentToolContext,
    handle: SandboxHandle,
    pkg: PackageInfo,
    cmd_name: str,
    args_json: str,
    *,
    exec_timeout: float | None = None,
) -> ExecResult:
    """Run one tool command, with the item's environment variables.

    Every dispatch path funnels through here — the agent's call, the #285 chart
    re-render, and a WUI's `callTool` — because "inject at the call site" is only
    one place until it is two, and the second one is where it silently stops
    happening.

    The variables are named PER CALL rather than left in the sandbox: the tool
    and the agent share a uid, so anything on disk is readable by both or by
    neither. Passing them here means the agent's own `exec` has nothing to
    inherit and no file to open.
    """
    assert actx.sandbox is not None  # a provisioned tool implies a sandbox
    env = _tool_env(actx.user_env, item_id=actx.investigation_id)
    argv = [f"{pkg.install_dir}/launch", cmd_name, args_json or "{}"]
    # Named only when given: every other caller keeps the sandbox's own cap.
    budget = {} if exec_timeout is None else {"exec_timeout": exec_timeout}
    try:
        return await actx.sandbox.exec(
            handle, argv, on_output=actx.on_exec_output, env=env or None, **budget
        )
    except SandboxNotFound:
        # #797: this is the funnel EVERY third-party dispatch goes through — the
        # agent's tool call, the #285 chart re-render, the WUI's `callTool` — and
        # it holds a handle acquired earlier in the turn, so a reap between then
        # and now lands here exactly as it does on the shell `exec`. Recovering
        # one and not the other means a turn survives its own commands and dies
        # on the first provisioned tool.
        #
        # The bundle has to be re-mounted, which is what `rebuild=True` asks the
        # wake hook for; `pkg.install_dir` then resolves in the fresh sandbox.
        logger.warning(
            "tooling: sandbox was gone when dispatching %s — rebuilding and retrying once",
            cmd_name,
        )
        fresh = await actx.ensure_sandbox(rebuild=True)
        return await actx.sandbox.exec(
            fresh, argv, on_output=actx.on_exec_output, env=env or None, **budget
        )


#: The item a third-party command runs for, set by the platform on every
#: dispatch (docs/tool-authoring.md). A tool that keys its work by item — the job
#: template's sameness (docs/plan-job-tool-template.md D44) — reads it here.
#: Absent where no item is in play, and from anything the platform does not
#: dispatch (the agent's own `exec`, a workflow `run:` step); the MCP runner
#: sets none, so there a tool sees whatever the engineer's shell holds.
ITEM_ID_ENV = "WORKSPACE_ITEM_ID"


def _tool_env(user_env: dict[str, str], *, item_id: str | None = None) -> dict[str, str]:
    """This turn's variables, plus the list of which names they are, plus which
    item this is.

    "This turn's", not "the item's": since #714 the set can also carry values
    composed from the request behind the send, and the launcher has to restore
    those by name too — which it does for free, because the list is derived from
    whatever arrives here rather than from any one source.

    The launcher sets `HOME` / `PYTHONPATH` / `PIP_USER` for itself, and those
    run after we hand the process its environment — so a reserved name the user
    set would be overwritten, which is "stored, listed, no effect". The list
    lets the launcher snapshot exactly these on entry and put them back as the
    last thing before exec, keeping the decision that the user wins.

    The item id is the one name the user does NOT win: it is the platform's to
    say, so a person's variable of that name is dropped — from the values and
    from the list, or the launcher would restore it over the platform's last.
    """
    own = {name: value for name, value in user_env.items() if name != ITEM_ID_ENV}
    env = {**own, "SANDBOX_USER_ENV_KEYS": " ".join(own)} if own else {}
    if item_id:
        env[ITEM_ID_ENV] = item_id
    return env


async def _review_chart(
    actx: AgentToolContext,
    pkg: PackageInfo,
    cmd: CommandInfo,
    handle: SandboxHandle,
    args_json: str,
    result: ExecResult,
    text: str,
) -> tuple[str, str]:
    """Run the VLM self-review loop over a chart command's output and fold its
    outcome into the tool result text (best image path + a one-line summary).

    Returns `(text, best_image_path)`; the path is `""` when no review ran, so the
    caller declares the command's own images unchanged."""
    images = _images_from_stdout(result.stdout)
    if not images:
        return text, ""
    first = images[0]
    sandbox = actx.sandbox
    assert sandbox is not None  # provisioned tools imply a sandbox
    try:
        first_png = await sandbox.download(handle, "/" + first.lstrip("/"))
    except Exception:  # noqa: BLE001 — can't read the render back → skip review
        logger.warning(
            "registry: chart review skipped, cannot read render %s", first, exc_info=True
        )
        return text, ""
    try:
        base_args: dict[str, Any] = json.loads(args_json or "{}")
    except ValueError:
        logger.debug("registry: chart review args_json unparseable, using empty base args")
        base_args = {}

    async def render(style: dict[str, Any]) -> tuple[bytes, str]:
        merged = {**base_args, "style": style}
        r = await _exec_tool(actx, handle, pkg, cmd.name, json.dumps(merged))
        imgs = _images_from_stdout(r.stdout)
        path = imgs[0] if imgs else first
        png = await sandbox.download(handle, "/" + path.lstrip("/"))
        return png, path

    sink = actx.on_exec_output
    on_chunk = (lambda t, _r: sink(t.encode("utf-8"))) if sink is not None else None
    outcome = await run_review(
        initial_png=first_png,
        initial_path=first,
        render=render,
        describer=actx.describer,
        on_chunk=on_chunk,
    )
    if outcome.image_path != first:
        text = text.replace(first, outcome.image_path)
    return f"{text}\n\n{outcome.summary()}", outcome.image_path


async def _declare_images(actx: AgentToolContext, result: ExecResult, text: str, best: str) -> str:
    """`text` with the command's images declared for the chat to render.

    The images a plotting command reports on stdout are already structured, so the
    only thing missing was a channel the FE can trust — this puts them in the same
    one `show_file` writes. Each is resolved through the workspace facade, which is
    both the mime sniff and the existence check: a command can name a path it did
    not write, and a declared file that isn't there renders as a broken image.

    `best` (non-empty when the VLM review re-rendered) replaces the first image, so
    the declaration names the same chart the text does."""
    images = _images_from_stdout(result.stdout)
    if not images or actx.files is None or actx.investigation_id is None:
        return text
    if best:
        images[0] = best
    shown: list[dict[str, Any]] = []
    for path in images:
        entry = await describe_for_display(actx.files, actx.investigation_id, path)
        if entry:
            shown.append(entry)
        else:
            logger.info("registry: not declaring %s — no such file in the workspace", path)
    return declare_shown_files(text, shown)


def describe_command(pkg: PackageInfo, cmd: CommandInfo) -> str:
    """What the model is told about ``cmd`` — its author's words, then which
    tool it belongs to.

    The id the user sees in the picker is a package; what reaches the model is a
    command. With nothing joining them, "what is `rca-tools`?" is a question
    about a string the model has never been shown, and it can only guess.

    It goes in the DESCRIPTION rather than anywhere else because that is the one
    string that travels with the tool: the native tool schema takes it verbatim
    once the command is LOADED. A second home would be a second thing to keep
    in step with the tool list, and nothing would notice it drift.

    Two things it is no longer: the prompt section stopped rendering
    descriptions, and an UNLOADED command is advertised by `_catalog_entry`
    (the author's first sentence + the bundle name) rather than by this text —
    both `docs/plan-tool-on-demand-loading.md`. So this is what the model reads
    about a command it has reached for, not about every command it is offered.

    The author's sentence stays first. It is what the model reads to decide
    whether to call this command at all; the bundle line answers a different
    question — who provides this — and burying the first under the second would
    trade a working inventory for a lookup nobody asked for yet.
    """
    # Every part after the id is optional and simply absent when unsaid. An
    # "unknown", or a `by ` with nothing after it, would be the platform
    # speaking for an author who said nothing.
    release = f" {pkg.version}" if pkg.version else ""
    by = f", by {pkg.author}" if pkg.author else ""
    said = f" — {pkg.description.strip()}" if pkg.description.strip() else "."
    line = f"{cmd.description}\n\nFrom the `{pkg.name}` tool bundle{release}{by}{said}"
    if pkg.latest_version is not None:
        # plan-tool-running-version D2: the release that RUNS is the one this
        # sandbox was created with; say so, and how the latest is reached, so
        # "which version is this?" gets the true answer (D1: told, not forced).
        # "Not the latest", never "older": a sha says different, not which way.
        running = pkg.version or "an unrecorded release"
        latest = f" ({pkg.latest_version})" if pkg.latest_version else ""
        line += (
            f" This workspace's sandbox runs {running}, not the latest release{latest}."
            " Closing the sandbox lets the next one be set up with the latest release."
        )
    return line


#: How long one catalog entry's description may be
#: (`docs/plan-tool-on-demand-loading.md`, decided with the operator: 100).
CATALOG_DESCRIPTION_LIMIT = 100

#: Ends a sentence wherever it appears — CJK punctuation is not followed by a
#: space, so a lookahead would make `。` end nothing in a Chinese description.
_SENTENCE_END_CJK = "。！？"
#: Ends a sentence only before whitespace or the end, so `3.5` does not — and
#: only when what follows looks like a new sentence and what precedes is not an
#: abbreviation, so `e.g.` / `etc.` / `U.S.` / `Max.` do not either. The first
#: version of this comment claimed `e.g.` was covered by the lookahead alone;
#: it was not, and `'Plot a chart, e.g. a trend…'` became `'Plot a chart, e.g.'`
#: — an 18-character catalog line, which is the only thing the model reads to
#: decide whether to reach for the command (round 1 A9).
_SENTENCE_END_ASCII = ".!?"

#: Words that end in `.` without ending a sentence. Lower-cased, dots stripped,
#: so `e.g` covers `e.g.` and `E.g.`. Anything with an internal dot (`U.S.`,
#: `i.e.`) and any single letter (`A.`) is caught by rule, not by this list.
_ABBREVIATIONS = frozenset(
    [
        "e.g",
        "i.e",
        "etc",
        "vs",
        "cf",
        "al",
        "approx",
        "incl",
        "excl",
        "resp",
        "fig",
        "no",
        "nos",
        "vol",
        "ca",
        "max",
        "min",
        "avg",
        "std",
        "dept",
        "est",
        "dr",
        "mr",
        "mrs",
        "ms",
        "prof",
        "inc",
        "ltd",
        "co",
        "corp",
        "jr",
        "sr",
        "st",
    ]
)
#: Where a too-long sentence may be cut: a mark the AUTHOR wrote, so the cut
#: lands between clauses instead of between two characters of one.
_CLAUSE_END = "，、；：,;"


def _looks_like_an_abbreviation(said: str, dot: int) -> bool:
    """Whether the `.` at `dot` is part of a word that is not a sentence end.

    Three rules, in the order they fire: the word already contains a dot
    (`i.e.`, `U.S.`), it is a single letter (`A.`), or it is one of the few
    spellings people actually write (`etc.`, `vs.`, `Max.`)."""
    start = dot
    while start > 0 and not said[start - 1].isspace():
        start -= 1
    word = said[start:dot]
    if "." in word:
        return True
    bare = word.strip("(）)[]「」\"'“”").lower()
    return len(bare) == 1 or bare in _ABBREVIATIONS


def _first_sentence(said: str) -> str:
    """The author's first sentence. A newline ends one too — plenty of
    descriptions are a title line followed by a paragraph.

    An ASCII `.` `!` `?` ends a sentence only when all three hold: whitespace
    (or the end) follows, the next non-space character starts something new
    (upper case or CJK — a digit or a lower-case word means the sentence
    continues: `Max. 1000`, `e.g. a trend`), and the word before the dot is not
    an abbreviation."""
    for i, ch in enumerate(said):
        if ch in _SENTENCE_END_CJK:
            return said[: i + 1]
        if ch == "\n":
            return said[:i].rstrip()
        if ch not in _SENTENCE_END_ASCII:
            continue
        rest = said[i + 1 :]
        if rest and not rest[0].isspace():
            continue  # `3.5`
        nxt = rest.lstrip()
        if ch == "." and nxt and not (nxt[0].isupper() or _is_cjk(nxt[0])):
            continue
        if ch == "." and _looks_like_an_abbreviation(said, i):
            continue
        return said[: i + 1]
    return said


def _is_cjk(ch: str) -> bool:
    return "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def catalog_description(text: str, *, limit: int = CATALOG_DESCRIPTION_LIMIT) -> str:
    """One catalog line's worth of what a command IS — the author's first
    sentence, shortened only if it has to be.

    A command the turn has not loaded is advertised by name + this line and NO
    parameter schema, which is what takes a six-figure system prompt back to a
    four-figure one. The line is what the model reads to decide whether to
    reach for this command at all, so it is not a character cut:

    - the first sentence is kept whole whenever it fits;
    - a longer one is cut at a boundary the author wrote — a space between
      words, or a clause mark (`，、；：,;`) — never between two characters of
      the same word or clause, and the ellipsis says it was shortened;
    - a run with no boundary at all inside the limit (a long unpunctuated
      Chinese sentence, a token) is cut at the limit. That is the last resort,
      not the rule.

    Nothing is lost by shortening: loading the command hands the model the full
    `describe_command` text and the real schema.
    """
    # `str(…)`, not a bare `.strip()`: a published manifest may carry a
    # command description that is `null`, a number or a list (`parse_manifest`
    # checks neither), and this is now on the path that builds the WHOLE tool
    # list — so an odd value used to be an ugly f-string and would now take
    # every turn on that item down with an AttributeError (round 1 A8).
    said = str(text or "").strip()
    if not said:
        return ""
    first = _first_sentence(said)
    # A limit too small to hold a boundary cut is not a limit to honour with
    # one: `first[: limit - 1]` turned `limit=0` into "keep everything but the
    # last character" and `limit=-5` into "drop five" (round 1 A16).
    if limit < 2:
        return "…" if said else ""
    if len(first) <= limit:
        return first
    kept = first[: limit - 1]
    cut = max(
        (i for i, ch in enumerate(kept) if ch.isspace() or ch in _CLAUSE_END),
        default=-1,
    )
    # A boundary in the first half keeps almost nothing — worse than the
    # no-boundary fallback, which keeps the whole budget. `'Plot ' + 200 chars`
    # kept 5 of 100 before this (round 1 A10).
    if cut >= 0 and cut * 2 >= limit:
        kept = kept[:cut].rstrip() if kept[cut].isspace() else kept[: cut + 1]
    return f"{_whole_clusters(kept)}…"


#: Characters that must not be the last thing before the ellipsis: a combining
#: mark or a joiner belongs to the character before it, and half a grapheme
#: cluster is mojibake, not a shortened sentence (round 1 A16).
_JOINERS = "\u200d\ufe0f\u20e3"


def _whole_clusters(kept: str) -> str:
    """`kept` with any trailing half-cluster removed."""
    while kept and (unicodedata.combining(kept[-1]) or kept[-1] in _JOINERS):
        kept = kept[:-1]
    # A regional-indicator pair is a flag; one on its own is half of one.
    if kept and "\U0001f1e6" <= kept[-1] <= "\U0001f1ff":
        pairs = 0
        i = len(kept) - 1
        while i >= 0 and "\U0001f1e6" <= kept[i] <= "\U0001f1ff":
            pairs += 1
            i -= 1
        if pairs % 2:
            kept = kept[:-1]
    return kept


#: The schema a catalog entry carries: none. `additionalProperties` is open
#: because the model WILL guess arguments at this entry — the name is right
#: there in front of it — and a provider enforcing a closed schema would reject
#: the call before `_stub_function_tool`'s handler could answer with the real
#: schema. The arguments are read by nothing; the answer is the schema.
_CATALOG_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": True}

#: What a catalog entry answers with when it is called. The full description
#: (the catalog line was only its first sentence), the real schema, and what to
#: do now — never a refusal: #537 measured that a model reads one as "stop
#: trying" and gives up on the whole source.
_LOAD_REPLY = (
    "`{name}` is now loaded for this turn. What it is:\n\n{described}\n\n"
    "Its arguments (JSON schema):\n\n{schema}\n\n"
    "Call `{name}` again with arguments matching that schema."
)


def load_reply(pkg: PackageInfo, cmd: CommandInfo) -> str:
    """The text a just-loaded command hands back. Pure, so the wording is
    testable without a sandbox or a turn."""
    return _LOAD_REPLY.format(
        name=model_tool_name(pkg, cmd),
        described=describe_command(pkg, cmd),
        schema=json.dumps(cmd.params_json_schema, ensure_ascii=False),
    )


def build_catalog_tools(
    packages: Sequence[PackageInfo],
    *,
    allowed: list[str] | None,
    mark_loaded: MarkLoaded,
) -> list[FunctionTool]:
    """One entry per granted command: the real name, one line of what it is, and
    no parameter schema (`docs/plan-tool-on-demand-loading.md`).

    This is what a turn sends INSTEAD of every command's full schema, which is
    the half that grows without bound — an operator reported a 414,115
    character system prompt. `mark_loaded` is
    called with the turn's context and the model name when an entry is invoked,
    so the next step of the same turn sends that command's real schema.

    Granted by the SAME `_granted` the executable builder uses, so the two
    lists name the same commands by construction.
    """
    return [_stub_function_tool(pkg, cmd, mark_loaded) for pkg, cmd in _granted(packages, allowed)]


#: What one `load_tool` call may answer with. The catalog's whole point is
#: that schemas are not in the window until they are needed; a single call
#: naming every bundle put 36,054 characters back (and `["sci-plot"] * 20` put
#: 261,238), in the tool result AND in the next step's tool list at once. The
#: built-ins' own ceiling (`cap_tool_outputs`, 200,000) does not reach here,
#: because the catalog tools are appended in `_agent_for` outside `build_tools`
#: (round 1 A7).
_LOAD_REPLY_MAX_CHARS = 20_000

#: How many names one call may ask for. 3,000 unknown names answered with
#: 928,888 characters before this.
_LOAD_MAX_NAMES = 20

#: How many names an unknown-name answer lists before it stops. A deployment
#: with hundreds of commands must not get its whole catalog pasted back into
#: the turn as an error message — but silence would leave the model guessing,
#: which is what this answers (`docs/plan-tool-on-demand-loading.md`).
_UNKNOWN_NAMES_SHOWN = 30

_LOAD_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Tool names to load, as they appear in your tool list. A bundle's "
                "own name loads every command in it."
            ),
        }
    },
    "required": ["names"],
}


def build_load_tool(
    packages: Sequence[PackageInfo],
    *,
    allowed: list[str] | None,
    mark_loaded: MarkLoaded,
) -> FunctionTool:
    """`load_tool`: ask for a command's real arguments before calling it.

    Calling an unloaded command already loads it and answers with its schema,
    so this is not the only way in — it is the way to look first, and to load
    a whole bundle in one step. Reads the SAME `_granted` as the catalog and
    the executable tools, so it can never reach a command this turn was not
    granted.
    """
    granted = _granted(packages, allowed)
    by_name: dict[str, list[tuple[PackageInfo, CommandInfo]]] = {}
    for pkg, cmd in granted:
        by_name.setdefault(model_tool_name(pkg, cmd), []).append((pkg, cmd))
        by_name.setdefault(pkg.name, []).append((pkg, cmd))
        by_name.setdefault(f"{pkg.name}:{cmd.name}", []).append((pkg, cmd))

    async def on_invoke(ctx: ToolContext[AgentToolContext], args_json: str) -> str:
        try:
            asked = json.loads(args_json or "{}").get("names") or []
        except ValueError:
            asked = []
        if isinstance(asked, str):  # a model that sent one name, not a list
            asked = [asked]
        # Deduped, and bounded: `["sci-plot"] * 20` is a plausible thing for a
        # model to emit, and every repeat used to cost another full schema.
        wanted: list[str] = []
        for a in asked:
            name = str(a)
            if name not in wanted:
                wanted.append(name)
        over = wanted[_LOAD_MAX_NAMES:]
        wanted = wanted[:_LOAD_MAX_NAMES]
        parts: list[str] = []
        done: set[str] = set()
        for name in wanted:
            found = by_name.get(name)
            if not found:
                parts.append(_unknown_name_reply(name, granted))
                continue
            for pkg, cmd in found:
                model_name = model_tool_name(pkg, cmd)
                mark_loaded(ctx, model_name)
                # A bundle and one of its commands can share a name (the
                # shipped `data-fetch` does), and both keys point at the same
                # command — answering twice doubled the schema for nothing.
                if model_name not in done:
                    done.add(model_name)
                    parts.append(load_reply(pkg, cmd))
        if over:
            # FIRST, not last: the length cap below trims the tail, and the one
            # sentence that must survive is the one saying why the model did
            # not get what it asked for. (A test caught this: the notice was
            # appended and then cut away.)
            parts.insert(
                0,
                f"Not loaded, too many names in one call (the limit is {_LOAD_MAX_NAMES}): "
                + ", ".join(f"`{n}`" for n in over[:_UNKNOWN_NAMES_SHOWN])
                + ". Ask for the ones you need.",
            )
        if not parts:
            return (
                "Nothing to load: `names` was empty. Give the name of a tool from "
                "your tool list, or a bundle's name to load all of its commands."
            )
        reply = "\n\n".join(parts)
        if len(reply) > _LOAD_REPLY_MAX_CHARS:
            reply = (
                reply[:_LOAD_REPLY_MAX_CHARS]
                + f"\n\n[cut at {_LOAD_REPLY_MAX_CHARS} characters — ask for fewer tools "
                "at a time to see the rest]"
            )
        return reply

    return FunctionTool(
        name="load_tool",
        description=(
            "Get a tool's usage (its argument schema) before calling it. Pass the "
            "names as they appear in your tool list; a bundle's name loads every "
            "command in it. Calling a tool directly also loads it."
        ),
        params_json_schema=_LOAD_TOOL_SCHEMA,
        on_invoke_tool=on_invoke,
        strict_json_schema=False,
    )


def _unknown_name_reply(name: str, granted: list[tuple[PackageInfo, CommandInfo]]) -> str:
    """Say what IS there. Silence here reads as "that tool is gone" and the
    model stops looking for the one it actually wanted."""
    names = sorted({model_tool_name(pkg, cmd) for pkg, cmd in granted})
    if not names:
        # Reachable only as a belt-and-braces: `load_tool` is no longer granted
        # to a turn with no package command (round 1 A6). The sentence below
        # would end in ": ." here, which is the silence this function exists to
        # avoid.
        return f"There is no tool called `{name}`, and this turn has no loadable tools."
    shown = ", ".join(f"`{n}`" for n in names[:_UNKNOWN_NAMES_SHOWN])
    more = (
        f" (and {len(names) - _UNKNOWN_NAMES_SHOWN} more in your tool list)"
        if len(names) > _UNKNOWN_NAMES_SHOWN
        else ""
    )
    return f"There is no tool called `{name}`. Tools you can load: {shown}{more}."


def _catalog_entry(pkg: PackageInfo, cmd: CommandInfo, name: str) -> str:
    """One catalog line: what the command is, and which bundle it comes from.

    The bundle name is there because `load_tool` advertises "a bundle's name
    loads every command in it" — and before this the turn was never told ANY
    bundle name (a third-party command carries it inside its own
    `<bundle>__<command>` name; a first-party one does not), so that path, the
    one escape from paying an extra step per command, could not be used
    (review round 1 A5). Version, author and the "not the latest release"
    notice stay in `describe_command`, which arrives when the command loads.
    """
    said = catalog_description(cmd.description)
    if name.startswith(f"{pkg.name}{THIRD_PARTY_SEP}"):
        return said  # the name already says it
    return f"{said} [{pkg.name}]" if said else f"[{pkg.name}]"


def _stub_function_tool(
    pkg: PackageInfo, cmd: CommandInfo, mark_loaded: MarkLoaded
) -> FunctionTool:
    name = model_tool_name(pkg, cmd)

    async def on_invoke(ctx: ToolContext[AgentToolContext], args_json: str) -> str:
        # The arguments are deliberately unread: whatever the model guessed, it
        # has not seen the schema yet. Marking first means a model that ignores
        # the instruction and calls a third time still progresses — the real
        # tool answers that one.
        mark_loaded(ctx, name)
        logger.info("registry: catalog hit %s — loading its schema for this turn", name)
        return load_reply(pkg, cmd)

    return FunctionTool(
        name=name,
        description=_catalog_entry(pkg, cmd, name),
        params_json_schema=_CATALOG_SCHEMA,
        on_invoke_tool=on_invoke,
        strict_json_schema=False,
    )


def _to_function_tool(pkg: PackageInfo, cmd: CommandInfo) -> FunctionTool:
    """Wrap ``cmd`` as a FunctionTool whose on_invoke execs
    ``[pkg.install_dir/launch, cmd.name, args_json]`` in the sandbox.
    Same exec-and-format pattern as the legacy provisioned tools, just
    with the JSON args passed straight through (no argv translation)."""

    # `ToolContext` (not `RunContextWrapper`) so the SDK doesn't downgrade
    # the context via `context._fork_with_tool_input(...)` — that strips
    # runtime metadata which `_FailureHandlingFunctionToolInvoker` and the
    # ask_knowledge_base bridge rely on. The args_recovery wrap already
    # carries this annotation, and in production every tool goes through
    # that wrap before reaching the SDK. Keeping the inner annotation
    # consistent means an off-path test or future refactor that bypasses
    # the wrap doesn't silently regress. See agents/tool.py
    # `_get_function_tool_invoke_context`.
    async def on_invoke(ctx: ToolContext[AgentToolContext], args_json: str) -> str:
        actx = ctx.context
        assert actx.sandbox is not None  # provisioned tools imply a sandbox
        handle = await actx.ensure_sandbox()
        logger.info("registry: dispatch %s:%s in sandbox %s", pkg.name, cmd.name, handle.id)
        # args_json is the raw JSON the LLM produced. We pass it through
        # verbatim — the tool's dispatcher pydantic-validates it; if it
        # fails, the tool prints a friendly error to stderr + exits 2.
        result = await _exec_tool(actx, handle, pkg, cmd.name, args_json)
        logger.info("registry: dispatch %s:%s exited %d", pkg.name, cmd.name, result.exit_code)
        # What `request_env` checks a variable name against (plan-env-request-card D3).
        actx.tool_outputs[model_tool_name(pkg, cmd)] = (result.stdout + result.stderr).decode(
            errors="replace"
        )
        text = _exec_result_text(actx, model_tool_name(pkg, cmd), result)
        # #285: a chart command that emits image(s) gets a VLM visual self-review
        # (detect layout issues → restyle → re-render, ≤2 passes) when a vision
        # model is wired. Gated on the command accepting a `style` override so a
        # re-render can actually adjust the layout.
        best = ""
        if actx.describer is not None and _accepts_style(cmd.params_json_schema):
            text, best = await _review_chart(actx, pkg, cmd, handle, args_json, result, text)
        return await _declare_images(actx, result, text, best)

    return FunctionTool(
        name=model_tool_name(pkg, cmd),
        description=describe_command(pkg, cmd),
        params_json_schema=cmd.params_json_schema,
        on_invoke_tool=on_invoke,
        # Same leniency as legacy provisioned tools — pydantic schemas
        # carry optional fields without strict-mode `nullable` gymnastics.
        strict_json_schema=False,
    )
