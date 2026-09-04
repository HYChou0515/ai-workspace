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

A third-party tool (#674/#724) is a row here like any other: its
``external_tools`` key IS an ``app.json`` ``tools[]`` entry, so it already has a
switch. What it carries in addition is provenance — which release resolved, who
published it, whether it came from the host's cached copy — because unlike an
app tool it ships on somebody else's schedule and can change between two turns
with nothing here edited. Listing it a second time somewhere else described one
tool twice, under the row that already governed it.

Every row says where it came from, so the answer is never inferred from an
absence: ``external`` separates "ours" from "a stranger's", which is not the
same question as whether an author happened to fill their name in.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from specstar import SpecStar

from ..apps.catalog import AppCatalog
from ..apps.manifest import load_app_manifest
from ..apps.profiles import load_profile
from ..apps.resolve import find_work_item
from ..sandbox.protocol import Sandbox
from ..tooling.catalog import flat_catalog, picker_units
from ..tooling.external import ExternalTools
from ..tooling.registry import PackageInfo
from .locator import ItemLocator
from .turn_context import resolve_item_tools

logger = logging.getLogger(__name__)


class ToolCatalogEntry(BaseModel):
    """One callable tool's display metadata (chat tool cards)."""

    name: str
    label: str
    description: str


class EnvNeedOut(BaseModel):
    """One environment variable a tool says it wants (#750).

    A hint, never a rule: nothing refuses a name absent from here, and a tool
    still receives every variable the item holds. It exists so the person
    filling the panel can stop guessing at names and meanings."""

    name: str
    description: str = ""
    required: bool | None = None
    """``None`` = the author did not mark it, which is neither required nor
    optional. The panel counts only ``True`` toward "still missing", so an
    author who never marks anything cannot make the panel cry wolf."""


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
    package: str | None = None
    """The package this row is ONE COMMAND of, when ``app.json`` granted at
    command granularity (a ``pkg:cmd`` entry). ``None`` when the row IS the
    package, or a built-in.

    Without it two rows can read as peers while one of them is a part of the
    other, and a command seen in a chat card cannot be traced to the switch
    that governs it."""
    external: bool = False
    """This tool's bytes come from a third-party artifact rather than the
    platform's own image (#674). Carried explicitly because "who wrote this"
    and "did its author fill their name in" are different questions, and
    reading the first off the absence of the second would answer them the
    same way."""
    version: str | None = None
    """The release that resolved for this item, for a third-party tool."""
    author: str | None = None
    """Who published it, as they wrote it in their own ``pyproject``. Shown,
    never trusted: identity is the certificate the platform signed."""
    stale: bool = False
    """Served from the host's last-known-good copy. Usable, but not
    necessarily the latest — and a version number that might be a release
    behind is worse than none if it does not say so."""
    unavailable: str | None = None
    """Why it could not be resolved, or ``None``. The row stays, because the
    tool is still in ``tools[]`` and still has a switch; what it has lost is
    the ability to run, and a row that looked ordinary would leave someone
    toggling it and wondering (#480)."""
    env_needs: list[EnvNeedOut] | None = None
    """What this tool says it wants from the item's environment (#750), or
    ``None`` when nobody said.

    ``None`` and ``[]`` carry different claims and the panel renders them
    differently: ``[]`` is "needs nothing", ``None`` is "did not say". Almost
    every package predates the declaration, so answering ``[]`` for silence
    would tell someone hunting for a missing variable that there is nothing to
    find — the one answer this endpoint must never invent.

    A built-in is ``[]`` on our own authority: `_exec_tool` is the only place
    `user_env` reaches a tool and it launches a package bundle, so a built-in
    receives none of these by construction."""


class ItemTools(BaseModel):
    tools: list[ItemToolState]


def _env_needs_of(unit_key: str, packages: Sequence[PackageInfo]) -> list[EnvNeedOut] | None:
    """What the tool behind this picker row wants from the environment (#750).

    A ``pkg:cmd`` row reports its PACKAGE's declaration: the variables are read
    by the bundle, and every command in it runs the same launcher with the same
    environment. Two commands of one package therefore report the same names —
    which is true, and is what lets the panel say who else is using a variable
    before someone clears it.

    A row whose package is not among ``packages`` is a built-in (or a package
    this deploy never built). Built-ins are ``[]`` — see ``ItemToolState``."""
    pkg = {p.name: p for p in packages}.get(unit_key.partition(":")[0])
    if pkg is None:
        return []
    if pkg.env_needs is None:
        return None
    # Coerce HERE, once, because this is where the typed model is built and
    # BOTH producers arrive at it: `packages` is `[*first-party, *third-party]`.
    # `EnvNeedOut.description` is a `str` and Pydantic does not coerce, so an
    # author writing `"description": null` used to raise inside the `rows`
    # comprehension below — which sits outside the resolve guard, with no
    # catch-all behind it, so one junk value returned 500 and took away the
    # whole picker and env panel for an item whose owner cannot edit that
    # package (#763).
    #
    # A guard on either producer alone leaves the other door open: the builder
    # refuses this content loudly, but only for artifacts built AFTER that
    # landed, and the first-party reader is deliberately lenient — it degrades
    # a malformed FILE to "nobody said" while passing a well-formed one's junk
    # VALUES straight through.
    #
    # Values, not rows: a name is what the panel is for, so a row keeps its
    # name and loses only the prose it could not carry.
    return [
        EnvNeedOut(
            name=n.name,
            description=n.description if isinstance(n.description, str) else "",
            required=n.required if isinstance(n.required, bool) else None,
        )
        for n in pkg.env_needs
    ]


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
        locator.require_item(slug, item_id)  # 404s a wrong slug→item pairing, 410s a deleted one
        found = find_work_item(spec, item_id)
        if found is None:
            # The gate said yes and this read says no: a delete landed between
            # the two. `assert` here was an AssertionError → 500 for a race the
            # caller can do nothing about, and it is the same answer the gate
            # itself would have given a moment later.
            raise HTTPException(status_code=410, detail=f"item {item_id!r} is gone")
        _, item = found
        prefs = item.attached_tool_prefs
        manifest = load_app_manifest(slug)
        ceiling = manifest.agent.tools
        prof = load_profile(slug, item.profile)
        default_set = set(prof.tools if prof.tools else ceiling)
        # Effective set from the very same resolve a turn uses (anti-drift).
        cfg = locator.resolve_agent_config(item_id)
        effective = set(cfg.allowed_tools or []) if cfg is not None else set()
        declared = manifest.agent.external_tools
        external = await _resolve_external(item_id, declared)
        # Resolved third-party packages join the first-party ones so their rows
        # get a real label and a description of what they bundle. Without them
        # a declared tool falls through `picker_units`' unknown-entry branch and
        # renders as a bare humanized key with nothing to say for itself.
        units = picker_units(ceiling, [*pkgs, *external.packages])
        rows = [
            _row(
                unit,
                default_on=unit.name in default_set,
                pref=_pref_state(prefs.get(unit.name)),
                effective=unit.name in effective,
                declared=declared,
                external=external,
                # The same package list `picker_units` described the rows from,
                # so a row's needs and its label can never come from different
                # resolutions of the same tool.
                packages=[*pkgs, *external.packages],
            )
            for unit in units
        ]
        _warn_undeclared(item_id, declared, {u.name for u in units})
        return ItemTools(tools=rows)

    async def _resolve_external(item_id: str, declared: dict[str, str]) -> ExternalTools:
        """This item's third-party tools, or an empty answer.

        Costs a host round-trip, and nothing for the apps that declare none —
        `resolve_item_tools` answers an empty declaration without asking. The
        alternative, reading a record of what the last turn got, would answer a
        different question: an author can publish between that turn and this
        read, and what the picker describes is the release that resolves now.

        A failure degrades to a refusal per declared tool. This request is the
        tool PICKER, and the pickable App tools have nothing to do with any
        artifact store — letting one unreachable host 500 the whole modal would
        take away the switches someone came here to press."""
        if not declared:
            return ExternalTools()
        try:
            return await resolve_item_tools(sandbox, locator, item_id)
        except Exception as exc:  # noqa: BLE001 - any failure degrades the same way
            logger.warning("item %s: third-party tools could not be resolved: %s", item_id, exc)
            return ExternalTools(refused=dict.fromkeys(declared, str(exc)))

    def _warn_undeclared(item_id: str, declared: dict[str, str], units: set[str]) -> None:
        """An `external_tools` key that is not in `tools[]` reaches no row —
        and no turn either, because `tools[]` is the ceiling every grant is
        drawn from. Silently absent in both places is the worst of both, so it
        is said once, here, where the mismatch is visible."""
        for name in sorted(set(declared) - units):
            logger.warning(
                "item %s: %r is declared in external_tools but not in tools[] — "
                "it is neither offered nor granted",
                item_id,
                name,
            )


def _row(
    unit,
    *,
    default_on: bool,
    pref: Literal["follow", "on", "off"],
    effective: bool,
    declared: dict[str, str],
    external: ExternalTools,
    packages: Sequence[PackageInfo] = (),
) -> ItemToolState:
    """One picker row, with the provenance of whatever provides it.

    A `pkg:cmd` entry is keyed by its package for provenance: the release and
    the author belong to the bundle, and every command in it came from the same
    artifact."""
    provider = unit.name.partition(":")[0]
    prov = external.provenance.get(provider)
    return ItemToolState(
        key=unit.name,
        label=unit.label,
        description=unit.description,
        default_on=default_on,
        pref=pref,
        effective=effective,
        package=unit.package,
        external=provider in declared,
        version=prov.version if prov else None,
        author=prov.author if prov else None,
        stale=bool(prov and prov.stale),
        unavailable=external.refused.get(provider),
        env_needs=_env_needs_of(unit.name, packages),
    )


def _pref_state(value: bool | None) -> Literal["follow", "on", "off"]:
    if value is None:
        return "follow"
    return "on" if value else "off"
