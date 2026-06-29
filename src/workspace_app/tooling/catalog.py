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
from collections.abc import Sequence
from dataclasses import dataclass

from .registry import PackageInfo

_WORD_SPLIT = re.compile(r"[_\-:]+")


@dataclass(frozen=True)
class ToolMeta:
    """Display metadata for one callable tool (built-in or package command)."""

    name: str
    label: str
    description: str


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


def _meta(name: str, description: str) -> ToolMeta:
    return ToolMeta(
        name=name,
        label=humanize_tool_label(name),
        description=summarize_description(description),
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
                )
            )
        elif entry in pkgs:
            cmds = pkgs[entry].commands
            granted = ", ".join(humanize_tool_label(c.name) for c in cmds)
            desc = f"Bundled tools: {granted}." if granted else ""
            units.append(ToolMeta(entry, humanize_tool_label(entry), desc))
        else:
            # Unknown entry (deploy without that package built) — still show it so
            # the user can toggle it; no description available.
            units.append(ToolMeta(entry, humanize_tool_label(entry), ""))
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
            out[cmd.name] = _meta(cmd.name, cmd.description)
    for name, desc in builtin_tool_descriptions().items():
        out[name] = _meta(name, desc)
    return out
