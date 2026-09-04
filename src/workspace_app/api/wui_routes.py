"""Run one package tool for an item, outside a turn (#WUI P4).

A WUI has no network of its own — its CSP forbids it — so reaching an external
system means asking the platform to run a **tool**, and this is where that
request lands. The point is not convenience: it is that credentials stay on the
platform (a tool reads them from the item's environment, #750) and never enter
a browser, and that a page cannot decide which credentials to ask a person for.

**What may be called is the app's decision, not the page's.** The ceiling is
``app.json``'s ``tools[]`` narrowed by the profile and the item's own toggles —
resolved through the SAME ``AppCatalog.resolve`` a turn uses, so a WUI can never
reach a tool the agent in that item could not. The page's own ``tools:``
declaration is disclosure rather than security: it is enforced in the bridge, so
a page cannot quietly use something it did not announce, but it can only ever
narrow what this route already allows.

Only **package** tools are reachable. A built-in is shaped for a model — it
truncates, it appends "[truncated: …]" to its own output, it returns errors as
prose — and a program that reads that gets JSON with a hole in it, silently. A
page needing a platform capability gets an HTTP route, not a built-in tool.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from ..agent.context import AgentToolContext
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..tooling.external import ExternalTools
from ..tooling.registry import PackageInfo, exec_package_command, find_allowed_command
from .locator import ItemLocator
from .turn_context import resolve_item_tools

logger = logging.getLogger(__name__)


class CallToolBody(BaseModel):
    args: dict[str, Any] = {}


class CallToolOut(BaseModel):
    """What the page gets back.

    The tool's stdout is handed over verbatim: the caller is a program, so
    anything we appended to explain a truncation would arrive as part of its
    data. `exit_code` carries the tool's own contract unchanged."""

    output: str
    exit_code: int


def register_wui_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    sandbox: Sandbox,
    registry: Any,
    packages: list[PackageInfo] | None,
    prebuilt_dir: Path | None,
    resolve_external: Callable[[str], Any] | None = None,
) -> None:
    """Mount the WUI tool-call route.

    ``resolve_external`` is a seam for tests; it defaults to the same host
    round-trip a turn makes."""

    # NOT "first party": in this codebase that names the bundled `sample-tools`
    # packages, which ARE reachable — the unreachable set is the agent's
    # BUILT-INS, and inverting the vocabulary here is how a future reader talks
    # themselves into the opposite rule.
    bundled: Sequence[PackageInfo] = packages or []

    async def _external(item_id: str) -> ExternalTools:
        if resolve_external is not None:
            return await resolve_external(item_id)
        return await resolve_item_tools(sandbox, locator, item_id)

    @app.post("/a/{slug}/items/{item_id}/wui/tools/{name}/call")
    async def wui_call_tool(slug: str, item_id: str, name: str, body: CallToolBody) -> CallToolOut:
        # A tool can write, so the caller needs the verb that lets them write —
        # the same authority they would need to make the change by hand.
        investigation_id = locator.require_access(slug, item_id, "edit_content")

        config = locator.resolve_agent_config(investigation_id)
        allowed = config.allowed_tools if config is not None else []

        external = await _external(investigation_id)
        available = [*bundled, *external.packages]

        try:
            found = find_allowed_command(available, allowed, name)
        except ValueError as exc:
            # Two packages exporting one command name: a deploy-configuration
            # fault, not this caller's. It breaks the agent's turn identically,
            # but an opaque 500 here reaches a person through the page's error
            # panel with nothing to act on.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if found is None:
            # Named rather than 404'd blank: this reaches a person through the
            # page's own error panel, and "which tool, and why not" is the whole
            # of what they can act on.
            if reason := external.refused.get(name.partition(":")[0]):
                raise HTTPException(status_code=409, detail=f"{name} is unavailable: {reason}")
            raise HTTPException(
                status_code=403,
                detail=f"This app does not offer {name} to its pages.",
            )
        pkg, command = found

        session = await registry.session(investigation_id)
        ctx = AgentToolContext(
            investigation_id=investigation_id,
            sandbox=sandbox,
            sandbox_spec=SandboxSpec(tools=external.shas),
            packages=list(available),
            agent_config=config,
            prebuilt_dir=prebuilt_dir,
            user_env=locator.env_vars_of(investigation_id),
            # The registry's own wake path, so this shares the item's ONE
            # sandbox with its turns rather than racing a second one into
            # existence beside it.
            ensure_sandbox_via=lambda on_progress, tools: registry.ensure_handle(
                session, tools=tools, on_progress=on_progress
            ),
        )
        handle = await ctx.ensure_sandbox()
        result = await exec_package_command(ctx, handle, pkg, command.name, json.dumps(body.args))
        logger.info(
            "wui: item %s ran %s (exit %s)", investigation_id, command.name, result.exit_code
        )
        return CallToolOut(
            output=result.stdout.decode("utf-8", "replace"), exit_code=result.exit_code
        )
