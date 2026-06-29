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
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.catalog import AppCatalog
from ..apps.manifest import load_app_manifest
from ..apps.profiles import load_profile
from ..apps.resolve import find_work_item
from ..tooling.catalog import flat_catalog, picker_units
from ..tooling.registry import PackageInfo
from .locator import ItemLocator


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


class ItemTools(BaseModel):
    tools: list[ItemToolState]


def register_tools_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    app_catalog: AppCatalog,
    packages: list[PackageInfo] | None,
    locator: ItemLocator,
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
        return ItemTools(tools=rows)


def _pref_state(value: bool | None) -> Literal["follow", "on", "off"]:
    if value is None:
        return "follow"
    return "on" if value else "off"
