"""#322: tool routes — the display catalog + the per-item picker state.

``GET /tools`` is the flat catalog (every callable tool → label + one-line
description) the chat **tool cards** label off, so an unmapped tool never leaks
its raw ``snake_case`` name into the UI.

``GET /a/{slug}/items/{item_id}/tools`` is the per-item **picker** state: one row
per ``app.json`` ``tools[]`` entry with its human label, the profile default
(``default_on``), the item's tri-state override (``pref``), and the resolved
``effective`` state. The effective state comes from the SAME
``AppCatalog.resolve`` a real turn uses, so the picker can never drift from the
toolset the agent actually runs.

The same response carries the app's **third-party** tools (#674/#724) in a
separate ``external`` list. Separate because they are not pickable: #674 P8
settled that they stay out of the picker rows, there being no switch to press.
What they need instead is disclosure — whose release this is and which one —
because unlike an app tool they ship on somebody else's schedule and can change
between two turns with nothing here edited.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.catalog import AppCatalog
from ..apps.manifest import load_app_manifest
from ..apps.profiles import load_profile
from ..apps.resolve import find_work_item
from ..sandbox.protocol import Sandbox
from ..tooling.catalog import flat_catalog, picker_units
from ..tooling.registry import PackageInfo
from .locator import ItemLocator
from .turn_context import resolve_item_tools

logger = logging.getLogger(__name__)


class ToolCatalogEntry(BaseModel):
    """One callable tool's display metadata (chat tool cards)."""

    name: str
    label: str
    description: str


class ItemToolState(BaseModel):
    """One pickable App tool's per-item state in the picker. ``pref`` is the
    stored tri-state override: ``follow`` (no override → tracks ``default_on``),
    ``on`` (forced on), or ``off`` (forced off). ``effective`` is the resolved
    result the agent runs with."""

    key: str
    label: str
    description: str
    default_on: bool
    pref: Literal["follow", "on", "off"]
    effective: bool


class ExternalToolState(BaseModel):
    """One third-party tool this App declares (#674/#724). Read-only: there is
    no per-item switch for these, so every field here answers "what am I
    actually running", not "what do I want".

    ``version``/``author`` are ``None`` together with a non-null
    ``unavailable`` — nothing resolved, so there is nothing to describe."""

    key: str
    version: str | None = None
    author: str | None = None
    """Who published it, as they wrote it in their own ``pyproject``. Shown,
    never trusted: identity is the certificate the platform signed."""
    stale: bool = False
    """Served from the host's last-known-good copy. Usable, but not
    necessarily the latest — and a version number that might be a release
    behind is worse than no version number if it does not say so."""
    unavailable: str | None = None
    """Why it could not be resolved, or ``None``. Listed rather than dropped:
    a declared tool that silently vanishes makes an outage look like a
    configuration the reader imagined (#480)."""


class ItemTools(BaseModel):
    tools: list[ItemToolState]
    external: list[ExternalToolState] = []


def register_tools_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    app_catalog: AppCatalog,
    packages: list[PackageInfo] | None,
    locator: ItemLocator,
    sandbox: Sandbox,
) -> None:
    pkgs = packages or []

    @app.get("/tools")
    async def tools_catalog() -> list[ToolCatalogEntry]:
        return [
            ToolCatalogEntry(name=m.name, label=m.label, description=m.description)
            for m in flat_catalog(pkgs).values()
        ]

    @app.get("/a/{slug}/items/{item_id}/tools")
    async def item_tools(slug: str, item_id: str) -> ItemTools:
        locator.require_item(slug, item_id)  # 404s a wrong slug→item pairing
        found = find_work_item(spec, item_id)
        assert found is not None  # require_item already validated it exists
        _, item = found
        prefs = item.attached_tool_prefs
        manifest = load_app_manifest(slug)
        ceiling = manifest.agent.tools
        prof = load_profile(slug, item.profile)
        default_set = set(prof.tools if prof.tools else ceiling)
        # Effective set from the very same resolve a turn uses (anti-drift).
        cfg = locator.resolve_agent_config(item_id)
        effective = set(cfg.allowed_tools or []) if cfg is not None else set()
        rows = [
            ItemToolState(
                key=unit.name,
                label=unit.label,
                description=unit.description,
                default_on=unit.name in default_set,
                pref=_pref_state(prefs.get(unit.name)),
                effective=unit.name in effective,
            )
            for unit in picker_units(ceiling, pkgs)
        ]
        return ItemTools(
            tools=rows,
            external=await _external_rows(item_id, manifest.agent.external_tools),
        )

    async def _external_rows(item_id: str, declared: dict[str, str]) -> list[ExternalToolState]:
        """Resolve the app's third-party declarations for this item.

        Costs a host round-trip, and nothing for the apps that declare none —
        `resolve_item_tools` answers an empty declaration without asking. The
        alternative, reading a record of what the last turn got, would answer a
        different question: an author can publish between that turn and this
        read, and what the picker offers to describe is the release that
        resolves now.

        A failure degrades to naming the declared tools with the reason. This
        request is the tool PICKER, and the pickable App tools have nothing to
        do with any artifact store — letting one unreachable host 500 the whole
        modal would take away the switches someone came here to press."""
        try:
            external = await resolve_item_tools(sandbox, locator, item_id)
        except Exception as exc:  # noqa: BLE001 - any failure degrades the same way
            logger.warning("item %s: third-party tools could not be resolved: %s", item_id, exc)
            return [ExternalToolState(key=name, unavailable=str(exc)) for name in sorted(declared)]
        rows = [
            ExternalToolState(
                key=name,
                version=prov.version,
                author=prov.author,
                stale=prov.stale,
            )
            for name, prov in external.provenance.items()
        ]
        rows += [
            ExternalToolState(key=name, unavailable=reason)
            for name, reason in external.refused.items()
        ]
        return sorted(rows, key=lambda r: r.key)


def _pref_state(value: bool | None) -> Literal["follow", "on", "off"]:
    if value is None:
        return "follow"
    return "on" if value else "off"
