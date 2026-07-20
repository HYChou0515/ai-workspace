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
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext

from ..agent.context import AgentToolContext
from ..agent.output_cap import cap_tool_outputs
from ..agent.plot_review import run_review
from ..agent.tools import _exec_result_text
from ..sandbox.protocol import ExecResult, SandboxHandle

logger = logging.getLogger(__name__)


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

    Raises ``ValueError`` if two *different* packages export the same
    command name within the same selection — the resulting flat name
    would shadow one tool, so we want the deployer to rename one."""
    if allowed is None:
        selected = [(pkg, cmd) for pkg in packages for cmd in pkg.commands]
    else:
        selected = _select_commands(packages, allowed)
    _check_collisions(selected)
    logger.debug("registry: %d function tool(s) selected (allowed=%s)", len(selected), allowed)
    # The same ceiling the built-ins get: a package command's stdout already
    # goes through `_exec_result_text`, but nothing structurally stops a future
    # command from answering with something bigger by another route.
    return cap_tool_outputs([_to_function_tool(pkg, cmd) for pkg, cmd in selected])


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


async def _review_chart(
    actx: AgentToolContext,
    pkg: PackageInfo,
    cmd: CommandInfo,
    handle: SandboxHandle,
    args_json: str,
    result: ExecResult,
    text: str,
) -> str:
    """Run the VLM self-review loop over a chart command's output and fold its
    outcome into the tool result text (best image path + a one-line summary)."""
    images = _images_from_stdout(result.stdout)
    if not images:
        return text
    first = images[0]
    sandbox = actx.sandbox
    assert sandbox is not None  # provisioned tools imply a sandbox
    try:
        first_png = await sandbox.download(handle, "/" + first.lstrip("/"))
    except Exception:  # noqa: BLE001 — can't read the render back → skip review
        logger.warning(
            "registry: chart review skipped, cannot read render %s", first, exc_info=True
        )
        return text
    try:
        base_args: dict[str, Any] = json.loads(args_json or "{}")
    except ValueError:
        logger.debug("registry: chart review args_json unparseable, using empty base args")
        base_args = {}

    async def render(style: dict[str, Any]) -> tuple[bytes, str]:
        merged = {**base_args, "style": style}
        r = await sandbox.exec(
            handle,
            [f"{pkg.install_dir}/launch", cmd.name, json.dumps(merged)],
            on_output=actx.on_exec_output,
        )
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
    return f"{text}\n\n{outcome.summary()}"


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
        result = await actx.sandbox.exec(
            handle,
            [f"{pkg.install_dir}/launch", cmd.name, args_json or "{}"],
            on_output=actx.on_exec_output,
        )
        logger.info("registry: dispatch %s:%s exited %d", pkg.name, cmd.name, result.exit_code)
        text = _exec_result_text(actx, cmd.name, result)
        # #285: a chart command that emits image(s) gets a VLM visual self-review
        # (detect layout issues → restyle → re-render, ≤2 passes) when a vision
        # model is wired. Gated on the command accepting a `style` override so a
        # re-render can actually adjust the layout.
        if actx.describer is not None and _accepts_style(cmd.params_json_schema):
            text = await _review_chart(actx, pkg, cmd, handle, args_json, result, text)
        return text

    return FunctionTool(
        name=cmd.name,
        description=cmd.description,
        params_json_schema=cmd.params_json_schema,
        on_invoke_tool=on_invoke,
        # Same leniency as legacy provisioned tools — pydantic schemas
        # carry optional fields without strict-mode `nullable` gymnastics.
        strict_json_schema=False,
    )
