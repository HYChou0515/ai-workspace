"""Serving runtime view plugins to the SPA (#847/#848 PR1 P3).

``GET /view-plugins`` lists what the operator installed; the SPA ``import()``s
each ``entry_url`` (relative to the API root — only the SPA knows the deploy's
sub-path) before its first render. ``GET /view-plugins/{name}/{path}`` serves
the plugin's ``web/`` folder read-only, and nothing outside it: the manifest,
the sandbox bundle and the skill are not the browser's business.

Both need a signed-in user. A plugin is operator-installed code that runs in the
SPA origin with the user's rights, so it is not secret — but there is no reason
to hand an anonymous caller the list of what is installed.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..agent.context import AgentToolContext
from ..sandbox.docker import DockerSandbox
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..tooling.external import ExternalTools
from ..tooling.registry import PackageInfo, exec_package_command
from ..view_plugins import ViewPlugin

logger = logging.getLogger(__name__)

#: One argv string may not exceed the kernel's MAX_ARG_STRLEN (128 KiB,
#: including its NUL). The args travel as ONE argv string to `launch`, so an
#: oversized call is refused here, with a message saying what to do instead,
#: rather than failing inside the sandbox as E2BIG.
ARGV_MAX = 128 * 1024

#: A command name is one plain word — never an option (`--help`) or anything a
#: shell or a CLI parser could read as more than the name.
_CMD = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ViewPluginOut(BaseModel):
    name: str
    #: The SDK major the plugin was built against; the SPA refuses a mismatch.
    sdk: str
    kinds: list[str]
    #: Relative to the API root (the SPA prepends its `API_PREFIX`).
    entry_url: str


def _web_file(plugin: ViewPlugin, rel: str) -> Path | None:
    """``rel`` under the plugin's ``web/``, or ``None`` if it names anything
    else — a missing file, a dir, or a path (``..``, absolute, a symlink) that
    resolves outside ``web/``."""
    web = plugin.web_dir.resolve()
    target = (web / rel).resolve()
    if web not in target.parents or not target.is_file():
        return None
    return target


def register_view_plugin_routes(
    app: FastAPI | APIRouter,
    *,
    get_plugins: Callable[[], Sequence[ViewPlugin]],
    get_user_id: Callable[[], str],
) -> None:
    @app.get("/view-plugins")
    async def list_view_plugins() -> list[ViewPluginOut]:
        get_user_id()
        return [
            ViewPluginOut(
                name=p.name,
                sdk=p.manifest.sdk,
                kinds=list(p.manifest.kinds),
                entry_url=f"/view-plugins/{p.name}/index.js",
            )
            for p in get_plugins()
        ]

    @app.get("/view-plugins/{name}/{path:path}")
    async def view_plugin_file(name: str, path: str) -> FileResponse:
        get_user_id()
        plugin = next((p for p in get_plugins() if p.name == name), None)
        target = None if plugin is None else _web_file(plugin, path)
        if target is None:
            raise HTTPException(status_code=404, detail="no such view plugin file")
        media = "text/javascript" if target.suffix in {".js", ".mjs"} else None
        return FileResponse(
            target,
            media_type=media or mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            # Fixed names: an operator's reinstall must reach the next page load.
            headers={"Cache-Control": "no-cache"},
        )


class RunBody(BaseModel):
    args: dict[str, Any] = {}


class RunOut(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


def register_view_plugin_runner(
    app: FastAPI | APIRouter,
    *,
    get_plugins: Callable[[], Sequence[ViewPlugin]],
    locator: Any,
    sandbox: Sandbox,
    registry: Any,
    resolve_tools: Callable[[str], Awaitable[ExternalTools]],
) -> None:
    """The one generic runner a plugin's web half calls (``useSandboxRun``).

    ``resolve_tools`` is what a turn resolves for the item — the app's
    third-party tools AND the view plugins' artifacts — so a sandbox this call
    has to create is the same sandbox a turn would have created, and a turn
    that comes later finds nothing missing. Plugin commands are not agent tools:
    nothing here touches an app's tool ceiling or the item's tool picker."""

    @app.post("/a/{slug}/items/{item_id}/view-plugins/{plugin}/{cmd}")
    async def run_view_plugin_command(
        slug: str, item_id: str, plugin: str, cmd: str, body: RunBody
    ) -> RunOut:
        # Drawing a view is reading the item; the command's output only reaches
        # the person who could already open the files it reads.
        investigation_id = locator.require_access(slug, item_id, "read_content")
        p = next((x for x in get_plugins() if x.name == plugin), None)
        if p is None:
            raise HTTPException(status_code=404, detail=f"no view plugin {plugin!r}")
        half = p.manifest.sandbox
        if half is None:
            raise HTTPException(
                status_code=404, detail=f"view plugin {plugin!r} has no sandbox commands"
            )
        if not _CMD.fullmatch(cmd):
            raise HTTPException(
                status_code=404, detail=f"view plugin {plugin!r} has no command {cmd!r}"
            )
        args_json = json.dumps(body.args)
        if len(args_json.encode()) >= ARGV_MAX:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"view plugin {plugin!r}: the arguments for {cmd!r} are "
                    f"{len(args_json.encode())} bytes, over the {ARGV_MAX}-byte argv limit — "
                    "write the data to a workspace file and pass its file path instead"
                ),
            )
        if isinstance(sandbox, DockerSandbox):
            raise HTTPException(
                status_code=501,
                detail=(
                    f"view plugin {plugin!r}: sandbox commands are not supported on "
                    "sandbox.kind docker (it has no tools support)"
                ),
            )
        if half.artifact and not getattr(sandbox, "resolves_tools", False):
            raise HTTPException(
                status_code=501,
                detail=(
                    f"view plugin {plugin!r}: its sandbox half is an artifact, which "
                    "needs the hosted sandbox backend (sandbox.kind http)"
                ),
            )
        external = await resolve_tools(investigation_id)
        if any(pkg.name == plugin for pkg in external.packages):
            # The app's own tool of that name owns `/.tools/<plugin>`: `launch`
            # there is THAT tool, reached past its picker and its verb.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"view plugin {plugin!r}: an app tool is also called {plugin!r}, so "
                    "its sandbox commands cannot be told apart — rename the plugin"
                ),
            )
        if half.artifact and plugin not in external.shas:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"view plugin {plugin!r}: its sandbox half could not be resolved by "
                    "the hosted sandbox (see the API log for the reason)"
                ),
            )
        session = await registry.session(investigation_id)
        if (
            half.artifact
            and session.handle is not None
            and session.tools is not None
            and plugin not in session.tools
        ):
            # A sandbox mounts its bundles when it is created and never again.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"view plugin {plugin!r}: this workspace was started before the "
                    "plugin was installed, so its commands are not in it. It works in a "
                    "new workspace, or in this one once it has been idle long enough "
                    "to be recycled."
                ),
            )
        ctx = AgentToolContext(
            investigation_id=investigation_id,
            sandbox=sandbox,
            sandbox_spec=SandboxSpec(tools=external.shas),
            # No item variables: this route is open to anyone who may READ the
            # item, and those are the owner's credentials for their own tools. A
            # plugin command reads workspace files, which a reader may see anyway.
            ensure_sandbox_via=lambda on_progress, tools: registry.ensure_handle(
                session, tools=tools, on_progress=on_progress
            ),
            prepare_env_via=lambda handle, on_output: registry.prepare_project_env(
                session, handle, on_output=on_output
            ),
        )
        # No project-env preparation: a plugin bundle carries its own python.
        handle = await ctx.ensure_sandbox(prepare_env=False)
        pkg = PackageInfo(name=p.name, install_dir=f"../.tools/{p.name}", commands=())
        result = await exec_package_command(ctx, handle, pkg, cmd, args_json)
        stderr = result.stderr.decode("utf-8", "replace")
        if result.exit_code == 127 and f".tools/{p.name}/launch" in stderr:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"view plugin {plugin!r}: its sandbox half is not installed in this "
                    "sandbox. Under sandbox.kind http a `bundle` plugin must be built into "
                    "sandbox-host's builtin/ tools. "
                    f"({stderr.strip()})"
                ),
            )
        logger.info(
            "view plugin %s: item %s ran %s (exit %s)",
            plugin,
            investigation_id,
            cmd,
            result.exit_code,
        )
        return RunOut(
            stdout=result.stdout.decode("utf-8", "replace"),
            stderr=stderr,
            exit_code=result.exit_code,
        )
