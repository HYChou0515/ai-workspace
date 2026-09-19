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
    """One display unit per ``app.json`` ``tools[]`` entry — the picker's
    pickable granularity (#322). The unit ``name`` IS the entry string verbatim,
    so a tri-state pref keyed by it lines up with what ``AppCatalog.resolve``
    adds/removes. A built-in or ``pkg:cmd`` entry resolves to that tool's meta; a
    bare package entry becomes one unit whose description lists the tools it
    bundles (so the user knows what a single checkbox grants)."""
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
    as written. Pure; both the runner and the picker route feed it the same
    package list, so the two never disagree on what a grant expands to."""
    by_name = {p.name: p for p in packages}
    out: list[str] = []
    for entry in entries:
        pkg = by_name.get(entry)
        if pkg is not None and ":" not in entry and pkg.commands:
            out.extend(f"{entry}:{c.name}" for c in pkg.commands)
        else:
            out.append(entry)
    return out


def unit_pref(unit: str, prefs: Mapping[str, bool]) -> bool | None:
    """The tri-state pin that governs one unit: its own key first, then — for a
    ``pkg:cmd`` unit — the package's key (an item pinned before commands were
    pickable holds ``{"rca-tools": false}``, and that goes on meaning the whole
    package), else ``None`` (follow the default)."""
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
    resolved its third-party packages — so the two places that DO hold the
    package list call this instead, and the picker's ``effective`` is by
    construction what the agent runs with."""
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
