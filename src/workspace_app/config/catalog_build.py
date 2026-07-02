"""Build a runtime `AgentConfigCatalog` from a typed `Settings.agents`.

For each usage entry (`workspace_chat[]`, `kb_chat`, or a template's
`_config.json`), the resolver:

  1. looks up the named preset from `agents.presets`
  2. merges the usage's override deltas onto the preset (Q5 rules)
  3. reads the `prompt_file` markdown body (Q6 resolution)
  4. constructs a typed `AgentConfig` (the same struct the runner
     consumes per turn)

This module owns step 1-4 as `resolve_usage(...)` plus a small
orchestrator `build_catalog(settings, config_dir) -> AgentConfigCatalog`
that turns the bundled / loaded Settings into the runtime catalog.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from ..agent.config_catalog import AgentConfigCatalog
from ..resources import AgentConfig
from ..resources.agent_config import Suggestion as ResourceSuggestion
from .merge import merge_layered
from .prompt_file import resolve_prompt_file
from .schema import Preset, Settings


def build_catalog(settings: Settings, config_dir: Path | None) -> AgentConfigCatalog:
    """Build the runtime `AgentConfigCatalog` from `settings.agents`.

    `config_dir` anchors relative `prompt_file:` paths (Q6). Pass the
    discovered config.yaml's parent directory in production; `None` is
    fine when only bundled / `pkg:`-form prompts are referenced (the
    bundled defaults all use `pkg:` so they work with `None`).

    Raises `ValueError` if the resolved `agents.kb_chat` doesn't grant
    `kb_search` — the KB sub-agent without that tool is broken by
    construction (it has no way to search the knowledge base) and
    silently degrades to "I can't access the KB" natural-language
    refusals. Better to fail the deploy loud at startup than to ship
    that footgun.
    """
    # B-flat: walk every purpose in `sub_agents` dynamically. Adding a
    # new sub-agent purpose (e.g. `qtime_pair_selector`) needs no edit
    # here; the operator's `agents.<new_purpose>: [...]` already landed
    # in `settings.agents.sub_agents` via the loader.
    by_purpose: dict[str, list[AgentConfig]] = {}
    for purpose, entries in settings.agents.sub_agents.items():
        if purpose in _KB_REQUIRED_PURPOSES and not entries:
            raise ValueError(_empty_required_message(purpose))
        configs: list[AgentConfig] = []
        for i, entry in enumerate(entries):
            default_name = _default_name(purpose, i)
            cfg = resolve_usage(
                entry,
                settings.agents.presets,
                config_dir=config_dir,
                default_name=default_name,
            )
            if purpose in _KB_REQUIRED_PURPOSES:
                _validate_kb_search_granted(cfg, entry, purpose=purpose, index=i)
            configs.append(cfg)
        by_purpose[purpose] = configs
    return AgentConfigCatalog(
        by_purpose=by_purpose,
        presets=settings.agents.presets,
        config_dir=config_dir,
    )


# Purposes whose AgentConfig MUST grant `kb_search` — the runner-level
# tool that lets the sub-agent query the knowledge base. Without it
# these sub-agents degrade to "I can't access the KB" refusals. Listed
# explicitly so the catalog builder doesn't have to guess from the
# preset name whether the sub-agent intends to talk to the KB.
_KB_REQUIRED_PURPOSES = frozenset({"kb_chat", "infer_modules"})


def _empty_required_message(purpose: str) -> str:
    if purpose == "kb_chat":
        return (
            "agents.kb_chat is empty — at least one entry is required so "
            "the FE picker has a default. Add `kb_chat: [{preset: kb-default}]`."
        )
    if purpose == "infer_modules":
        return (
            "agents.infer_modules is empty — at least one entry is required "
            "so the RCA agent's `infer_modules` tool has a sub-agent to "
            "delegate to. Add `infer_modules: [{preset: infer-modules-default}]`."
        )
    return f"agents.{purpose} is empty — at least one entry is required."


def _default_name(purpose: str, index: int) -> str | None:
    """The label the FE picker shows when a usage entry didn't specify
    `name`."""
    if purpose == "kb_chat":
        return "KB Agent" if index == 0 else f"KB Agent {index}"
    if purpose == "infer_modules":
        return "Infer Modules" if index == 0 else f"Infer Modules {index}"
    # New purposes ship with no default-name policy; resolve_usage falls
    # back to the preset name when both `name` and `default_name` are
    # absent, which is fine for one-off sub-agents.
    return None


def _validate_kb_search_granted(
    resolved: AgentConfig,
    usage: dict[str, Any],
    *,
    purpose: str,
    index: int = 0,
) -> None:
    """Loud check that a resolved KB-facing sub-agent AgentConfig
    actually grants `kb_search`. Catches two real footguns:

    - Operator points the usage at a preset that doesn't declare
      `allowed_tools` — the runner exposes no tools.
    - Operator explicitly writes `allowed_tools: []` at the usage.

    Either way `kb_search` is absent and the sub-agent silently
    degrades to "I can't access the KB" refusals. The hint names the
    preset so the operator knows where to add `kb_search`."""
    tools = resolved.allowed_tools or []
    if "kb_search" not in tools:
        preset_name = usage.get("preset", "<unset>")
        raise ValueError(
            f"agents.{purpose}[{index}] resolves to allowed_tools="
            f"{resolved.allowed_tools!r}, which does not include `kb_search` "
            f"— the sub-agent has no way to query the knowledge base. "
            f"Add `allowed_tools: [kb_search]` either on the referenced "
            f"preset ({preset_name!r}) or as a usage-level override under "
            f"`agents.{purpose}[{index}]`."
        )


def _to_suggestion(s: Any) -> ResourceSuggestion:
    """Normalise a config suggestion into a `Suggestion` struct. Accepts the
    structured `{label, prompt}` map *and* (back-compat, #96) a bare string —
    the old shorthand where one string is both the chip label and the prompt.
    User config usages can carry raw strings that list-replace the preset's
    already-normalised suggestions, so they reach here un-normalised."""
    if isinstance(s, str):
        return ResourceSuggestion(label=s, prompt=s)
    return ResourceSuggestion(label=s["label"], prompt=s["prompt"])


def resolve_usage(
    usage: dict[str, Any],
    presets: dict[str, Preset],
    *,
    config_dir: Path | None,
    default_name: str | None = None,
) -> AgentConfig:
    """Apply Q5 merge rules to a usage dict against its named preset
    and return a typed `AgentConfig`.

    The usage dict is `{ preset: <name>, name?: <label>, ...overrides }`.
    Validation (preset exists, required fields present) is done by the
    loader's strict-validation stage; here we assume well-formed input.
    `default_name` is used when the usage didn't specify one — typical
    for kb_chat which doesn't need a picker label.
    """
    preset_name = usage["preset"]
    preset = presets[preset_name]
    preset_dict = dataclasses.asdict(preset)
    # Strip the reference key and any usage-only fields before merge so
    # the merge target keys all come from the same name space.
    overrides = {k: v for k, v in usage.items() if k not in {"preset", "name"}}
    merged = merge_layered(preset_dict, overrides)
    # Resolve prompt_file → body text. config_dir anchors relative
    # paths (Q6); pkg:/absolute forms don't need it.
    prompt_text = resolve_prompt_file(merged["prompt_file"], config_dir=config_dir)
    llm = merged.get("llm", {}) if isinstance(merged.get("llm"), dict) else {}
    # allowed_tools is tri-state (Q4-followup): None = "haven't specified"
    # (runner uses defaults), [] = explicit empty, [...] = exact. Preserve
    # None verbatim so the runner sees the operator's intent.
    raw_at = merged.get("allowed_tools")
    allowed_tools = list(raw_at) if raw_at is not None else None
    return AgentConfig(
        name=usage.get("name") or default_name or preset_name,
        model=merged["model"],
        system_prompt=prompt_text,
        description=merged.get("description", ""),
        # ``dataclasses.asdict(preset)`` above flattens the loader-side
        # Suggestion dataclass into a plain dict. Convert back to the
        # msgspec.Struct form the AgentConfig field expects. See #91.
        suggestions=[_to_suggestion(s) for s in merged.get("suggestions", [])],
        allowed_tools=allowed_tools,
        env=dict(merged.get("env", {})),
        sandbox_image=merged.get("sandbox_image", "workspace-app/sandbox:py312-ds"),
        idle_timeout_seconds=merged.get("idle_timeout_seconds", 28800),
        llm_base_url=llm.get("base_url", ""),
        llm_api_key=llm.get("api_key", ""),
        # #113 Layer 1: anti-repetition sampling penalties (None = inherit).
        frequency_penalty=llm.get("frequency_penalty"),
        presence_penalty=llm.get("presence_penalty"),
        repetition_penalty=llm.get("repetition_penalty"),
        # #107 request hygiene: sampling/output knobs (None = param not sent).
        temperature=llm.get("temperature"),
        top_p=llm.get("top_p"),
        max_tokens=llm.get("max_tokens"),
    )
