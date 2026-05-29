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
command. Cross-package command-name collisions raise so the deployer
gets a clear signal at startup, not opaque LLM confusion at runtime.

See `docs/plan-skills-and-tools.md` §B.3.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import FunctionTool, RunContextWrapper

from ..agent.context import AgentToolContext
from ..agent.tools import _format_exec


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
class PackageInfo:
    """One prebuilt package the sandbox can run. ``install_dir`` is the
    *sandbox-relative* path the launch binary lives at — the sandbox
    bind-mounts ``PREBUILT_DIR/<name>`` at ``/.tools/<name>`` (reached
    as ``../.tools/<name>`` from the workspace at ``/root``)."""

    name: str
    commands: tuple[CommandInfo, ...]
    install_dir: str


def discover_packages(prebuilt_dir: Path) -> list[PackageInfo]:
    """Walk ``prebuilt_dir`` and return one ``PackageInfo`` per subdir
    that has a ``commands.json``. Subdirs without one are skipped silently
    (half-built bundles never half-advertise themselves). Returns ``[]``
    when ``prebuilt_dir`` doesn't exist — common in tests + a fresh
    deploy without prebuild yet."""
    if not prebuilt_dir.is_dir():
        return []
    out: list[PackageInfo] = []
    for sub in sorted(prebuilt_dir.iterdir()):
        if not sub.is_dir():
            continue
        commands_json = sub / "commands.json"
        schemas_dir = sub / "schemas"
        if not commands_json.is_file() or not schemas_dir.is_dir():
            continue
        meta = json.loads(commands_json.read_text())
        commands: list[CommandInfo] = []
        for entry in sorted(meta, key=lambda m: m["name"]):
            schema_path = schemas_dir / f"{entry['name']}.json"
            if not schema_path.is_file():
                continue  # half-built — skip silently
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
            )
        )
    return out


def build_function_tools(
    packages: Sequence[PackageInfo],
    *,
    allowed: list[str] | None,
) -> list[FunctionTool]:
    """Expand ``allowed`` (colon syntax: ``"pkg"`` for the full package,
    ``"pkg:cmd"`` for one command) into FunctionTools.

    ``allowed=None`` → ``[]`` (no provisioned tools requested; common for
    the default template). Unknown package names / command names are
    silently skipped — mirrors the legacy ``build_tools`` behaviour and
    keeps the LLM from seeing 500s for a config typo.

    Raises ``ValueError`` if two *different* packages export the same
    command name within the same selection — the resulting flat name
    would shadow one tool, so we want the deployer to rename one."""
    if allowed is None:
        return []
    selected = _select_commands(packages, allowed)
    _check_collisions(selected)
    return [_to_function_tool(pkg, cmd) for pkg, cmd in selected]


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
            continue  # unknown package — silently skip
        if cmd_name is None:
            out.extend((pkg, c) for c in pkg.commands)
            continue
        cmd = next((c for c in pkg.commands if c.name == cmd_name), None)
        if cmd is None:
            continue  # unknown command — silently skip
        out.append((pkg, cmd))
    return out


def _check_collisions(selected: list[tuple[PackageInfo, CommandInfo]]) -> None:
    """If two different packages export a command with the same name in
    the selected set, raise a clear ValueError listing both packages."""
    by_cmd: dict[str, list[str]] = {}
    for pkg, cmd in selected:
        by_cmd.setdefault(cmd.name, []).append(pkg.name)
    collisions = {cmd_name: pkgs for cmd_name, pkgs in by_cmd.items() if len(set(pkgs)) > 1}
    if collisions:
        msg = "; ".join(
            f"command {cmd!r} appears in packages {sorted(set(pkgs))}"
            for cmd, pkgs in collisions.items()
        )
        raise ValueError(f"cross-package tool name collision: {msg}")


def _to_function_tool(pkg: PackageInfo, cmd: CommandInfo) -> FunctionTool:
    """Wrap ``cmd`` as a FunctionTool whose on_invoke execs
    ``[pkg.install_dir/launch, cmd.name, args_json]`` in the sandbox.
    Same exec-and-format pattern as the legacy provisioned tools, just
    with the JSON args passed straight through (no argv translation)."""

    async def on_invoke(ctx: RunContextWrapper[AgentToolContext], args_json: str) -> str:
        actx = ctx.context
        assert actx.sandbox is not None  # provisioned tools imply a sandbox
        handle = await actx.ensure_sandbox()
        # args_json is the raw JSON the LLM produced. We pass it through
        # verbatim — the tool's dispatcher pydantic-validates it; if it
        # fails, the tool prints a friendly error to stderr + exits 2.
        result = await actx.sandbox.exec(
            handle,
            [f"{pkg.install_dir}/launch", cmd.name, args_json or "{}"],
            on_output=actx.on_exec_output,
        )
        return _format_exec(result)

    return FunctionTool(
        name=cmd.name,
        description=cmd.description,
        params_json_schema=cmd.params_json_schema,
        on_invoke_tool=on_invoke,
        # Same leniency as legacy provisioned tools — pydantic schemas
        # carry optional fields without strict-mode `nullable` gymnastics.
        strict_json_schema=False,
    )
