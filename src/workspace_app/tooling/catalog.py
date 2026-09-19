"""Tool catalog (#322) — one source of truth for tool *display* metadata.

A ``ToolMeta`` is ``{name, label, description}`` for one callable tool: a
built-in (``exec`` / ``ask_knowledge_base`` / …) or a package command
(``data-fetch`` / a ``rca-tools`` sub-command). ``label`` is a human-readable
name (humanized from the tool name — the guaranteed, never-raw-snake_case
fallback the FE i18n layer overlays nicer localized strings on top of, #322
Q5); ``description`` is the first sentence of the tool's own docstring /
``commands.json`` description.

Consumed by:
- the web **tool picker** (per-App, via ``picker_units``) — the per-item
  tri-state toolset override, and
- the chat **tool cards** (via ``flat_catalog``) — so an unmapped tool no
  longer leaks its raw ``snake_case`` name into the UI.

Both read the SAME catalog, so the picker and the cards never drift from each
other or from the tools the agent actually runs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .registry import PackageInfo

_WORD_SPLIT = re.compile(r"[_\-:]+")


BUILTIN_GROUP = "builtin"
"""The one picker fold every built-in tool shares (#322 grouping)."""


@dataclass(frozen=True)
class ToolMeta:
    """Display metadata for one callable tool (built-in or package command)."""

    name: str
    label: str
    description: str
    package: str | None = None
    """The label of the package this unit is ONE COMMAND of (a ``pkg:cmd``
    entry), or ``None`` when the unit is the whole package or a built-in.

    The picker grants at whichever granularity ``app.json`` chose, so two rows
    side by side can mean "this whole bundle" and "this one command of that
    bundle". Told only its command name, a reader cannot tell which — nor which
    row's switch governs the tool they saw in a chat card."""
    group: str = BUILTIN_GROUP
    """Which fold of the picker this unit lives under: ``"builtin"`` for every
    built-in, otherwise the raw package id (a ``pkg:cmd`` command and its
    whole-package row share it; an entry nothing resolves is its own group).

    Named here, on the server, because the FE cannot tell a whole-package row
    of a first-party package from a built-in — both carry no ``package`` and
    no ``external`` flag. The raw id, not the humanized label, so the fold key
    is stable whatever locale renders it."""


def humanize_tool_label(name: str) -> str:
    """Turn a tool/command id into a human label — the guaranteed fallback that
    keeps a raw ``snake_case`` / ``kebab-case`` name from ever reaching the UI.

    ``"ask_knowledge_base"`` → ``"Ask Knowledge Base"``; ``"rca-tools"`` →
    ``"Rca Tools"``; ``"data-fetch"`` → ``"Data Fetch"``. The FE i18n layer
    overlays nicer / localized labels for curated tools on top of this."""
    words = [w for w in _WORD_SPLIT.split(name) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or name


def summarize_description(text: str) -> str:
    """A one-line summary for a tool card / picker row: the first sentence of the
    full (multi-line) description, whitespace-collapsed. Empty stays empty."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    idx = flat.find(". ")
    if idx != -1:
        return flat[: idx + 1]
    return flat


def _meta(name: str, description: str, *, group: str = BUILTIN_GROUP) -> ToolMeta:
    return ToolMeta(
        name=name,
        label=humanize_tool_label(name),
        description=summarize_description(description),
        group=group,
    )


def picker_units(app_tools: Sequence[str], packages: Sequence[PackageInfo]) -> list[ToolMeta]:
    """One display unit per entry of ``app_tools`` — the picker's pickable
    granularity (#322). The route hands it the ceiling already brought to
    command granularity (``expand_entries``), so a whole-package grant arrives
    as ``pkg:cmd`` entries and draws one row per command; a bare package entry
    reaches here only when the package has no commands or is unknown. The unit
    ``name`` IS the entry string verbatim, so a tri-state pref keyed by it is
    what ``unit_pref`` reads. A built-in or ``pkg:cmd`` entry resolves to that
    tool's meta; a bare package entry becomes one unit whose description lists
    the tools it bundles (so the user knows what a single checkbox grants)."""
    from ..agent.tools import builtin_tool_descriptions

    builtins = builtin_tool_descriptions()
    pkgs = {p.name: p for p in packages}
    units: list[ToolMeta] = []
    for entry in app_tools:
        if entry in builtins:
            units.append(_meta(entry, builtins[entry]))
        elif ":" in entry:
            pkg_name, _, cmd_name = entry.partition(":")
            pkg = pkgs.get(pkg_name)
            cmd = next((c for c in pkg.commands if c.name == cmd_name), None) if pkg else None
            units.append(
                ToolMeta(
                    entry,
                    humanize_tool_label(cmd_name),
                    summarize_description(cmd.description if cmd else ""),
                    package=humanize_tool_label(pkg_name),
                    group=pkg_name,
                )
            )
        elif entry in pkgs:
            pkg = pkgs[entry]
            # The author's own words when they wrote any. Listing the commands
            # is what the platform says when nobody said anything better — an
            # inventory, not a purpose, and the command names are one click
            # away anyway.
            granted = ", ".join(humanize_tool_label(c.name) for c in pkg.commands)
            desc = pkg.description.strip() or (f"Bundled tools: {granted}." if granted else "")
            units.append(ToolMeta(entry, humanize_tool_label(entry), desc, group=entry))
        else:
            # Unknown entry (deploy without that package built) — still show it so
            # the user can toggle it; no description available.
            units.append(ToolMeta(entry, humanize_tool_label(entry), "", group=entry))
    return units


def flat_catalog(packages: Sequence[PackageInfo]) -> dict[str, ToolMeta]:
    """Every callable tool name → its ``ToolMeta``: built-ins (by registered
    name) plus every command of every provisioned package (by command name —
    what the LLM actually calls, so a tool card can look it up). Built-in names
    win on the (deliberately avoided) name collision."""
    from ..agent.tools import builtin_tool_descriptions

    out: dict[str, ToolMeta] = {}
    for pkg in packages:
        for cmd in pkg.commands:
            out[cmd.name] = _meta(cmd.name, cmd.description, group=pkg.name)
    for name, desc in builtin_tool_descriptions().items():
        out[name] = _meta(name, desc)
    return out


def expand_entries(entries: Iterable[str], packages: Sequence[PackageInfo]) -> list[str]:
    """Bring ``app.json`` entries to COMMAND granularity, in order.

    A whole-package entry becomes one ``pkg:cmd`` per command the package has
    right now — "the whole package" keeps meaning exactly that as releases add
    commands, since nothing is enumerated in the manifest. A package that
    exports no command (a runtime carrier such as ``python-stack``) stays one
    unit, or its switch would have nothing to hang on. Built-ins, entries
    already at command granularity, and entries nothing resolves pass through
    as written; an entry that names a built-in is a built-in even when a
    package shares the name (the runner's ``dedupe_tools`` lets the built-in
    outrank a package's copy, so the picker draws the same winner). Deduped,
    first position wins: ``["rca-tools", "rca-tools:spc"]`` grants ``spc``
    once, not a duplicate row and a FunctionTool built twice. Pure; every
    door that holds the package list (`apps.catalog.finalize_tool_grants`
    and the picker route) feeds it the same list, so nothing disagrees on
    what a grant expands to."""
    from ..agent.tools import builtin_tool_descriptions

    builtins = builtin_tool_descriptions()
    by_name = {p.name: p for p in packages}
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        pkg = by_name.get(entry)
        if pkg is not None and ":" not in entry and entry not in builtins and pkg.commands:
            units = [f"{entry}:{c.name}" for c in pkg.commands]
        else:
            units = [entry]
        for unit in units:
            if unit not in seen:
                seen.add(unit)
                out.append(unit)
    return out


def narrow_entries(entries: Iterable[str], held: Iterable[str]) -> list[str]:
    """``entries`` ∩ ``held``, at whichever granularity each side is written —
    the one rule behind every "a declared list, bounded by what this turn
    holds": a workflow step's ``tools:``, a sub-agent definition's ``tools``
    (at load and at ``save_subagent``).

    An entry that is held verbatim stays. A bare ``pkg`` becomes the commands
    of it that are held (``pkg:cmd`` units, by name), so an item's pins bind
    the delegate too — a step that says ``rca-tools`` on an item that pinned
    ``pareto`` off does not get ``pareto``. A ``pkg:cmd`` entry stays when the
    bare ``pkg`` is held (a config that never met its package list). Anything
    else is dropped. Deduped, in ``entries`` order. String-level on purpose:
    it needs no package list, so it can run wherever the held list is."""
    held_set = set(held)
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry in held_set:
            units = [entry]
        elif ":" not in entry:
            units = sorted(h for h in held_set if ":" in h and h.partition(":")[0] == entry)
        elif entry.partition(":")[0] in held_set:
            units = [entry]
        else:
            units = []
        for unit in units:
            if unit not in seen:
                seen.add(unit)
                out.append(unit)
    return out


def unit_pref(unit: str, prefs: Mapping[str, bool]) -> bool | None:
    """The tri-state pin that governs one unit: its own key first, then — for a
    ``pkg:cmd`` unit — the package's key (an item pinned before commands were
    pickable holds ``{"rca-tools": false}``, and that goes on meaning the whole
    package), else ``None`` (follow the default).

    The package key governs a ``pkg:cmd`` unit whatever granularity the App
    granted at — including a package the App later narrowed to one command. A
    stored "this package is off" outlives the App changing how much of the
    package it grants; before part 2 such a key was a no-op there (it named no
    ceiling entry), and that is the one reading that changed."""
    if unit in prefs:
        return prefs[unit]
    pkg, sep, _ = unit.partition(":")
    if sep and pkg in prefs:
        return prefs[pkg]
    return None


@dataclass(frozen=True)
class CommandGrants:
    """What a ceiling + default set + prefs resolve to, at command granularity.

    ``enabled`` is what the agent gets, ``disabled`` the rest of the ceiling
    (the #480 "available on request" list); together they are the expanded
    ceiling in ceiling order, disjoint. ``default_on`` is the expanded default
    set ∩ ceiling — what a unit follows when nobody pinned it, which is what
    the picker shows beside "Default"."""

    enabled: tuple[str, ...]
    disabled: tuple[str, ...]
    default_on: frozenset[str]


def command_grants(
    ceiling: Iterable[str],
    default_entries: Iterable[str],
    prefs: Mapping[str, bool] | None,
    packages: Sequence[PackageInfo],
) -> CommandGrants:
    """The one rule behind the tool picker and the agent's toolset
    (plan-tools-picker-groups part 2): expand ``ceiling`` and
    ``default_entries`` to command granularity, then per unit take its pin
    (``unit_pref``) or, unpinned, whether the default set has it.

    ``AppCatalog.resolve`` cannot do this — it runs before the turn has
    resolved its third-party packages — so it is applied where the packages
    are: `apps.catalog.finalize_tool_grants` writes the answer INTO the
    config's ``allowed_tools`` / ``disabled_tools`` at every door that holds
    the package list, and the picker route reads the same finalized config,
    so its ``effective`` is by construction what the agent runs with."""
    units = expand_entries(ceiling, packages)
    default_units = set(expand_entries(default_entries, packages))
    pins = prefs or {}
    enabled: list[str] = []
    disabled: list[str] = []
    for unit in units:
        pinned = unit_pref(unit, pins)
        include = pinned if pinned is not None else unit in default_units
        (enabled if include else disabled).append(unit)
    return CommandGrants(
        enabled=tuple(enabled),
        disabled=tuple(disabled),
        default_on=frozenset(u for u in units if u in default_units),
    )
