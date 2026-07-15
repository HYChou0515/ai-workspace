"""AppCatalog — the per-turn 3-layer agent resolve (#89, decision 25).

``resolve(app_slug, profile, attached_preset)`` composes an ``AgentConfig`` from:

- **App** (``app.json``) — the *ceiling*: picker (allowed presets), tools, base
  ``prompt_file``, suggestions fallback.
- **profile** (``_profile.json``) — narrows tools/presets to a subset, supplies
  the prompt appendix + per-profile suggestions + default preset.
- **preset** (``agents.presets`` in config.yaml) — model + creds + sandbox image
  + idle timeout + env (decision ii).

``validate_function_coherence`` enforces decision 11 (a ``tools[]`` ↔ function
toggle mismatch is a startup hard error). In P3d the AppCatalog is constructible
via ``factories.get_app_catalog`` and ``validate_all_apps`` runs at startup
(``create_app``); the live picker/resolve cutover is P4.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import resources
from typing import TYPE_CHECKING

from msgspec import UNSET

from ..resources import AgentConfig
from .manifest import AppManifest, load_app_manifest
from .profiles import load_profile, load_profile_appendix
from .skills import SkillMeta, effective_item_skills

if TYPE_CHECKING:
    from ..config.schema import Preset

_APPS_PKG = "workspace_app.apps"

# Builtin file tools — meaningful only when the App enables `workspace`.
_FILE_TOOLS = frozenset(
    {"read_file", "write_file", "edit_file", "list_files", "exists", "delete_file"}
)
# Tools that need a compute sandbox. Package tools (data-fetch, …) also run in
# the sandbox, but their names are deploy-specific; `exec` is the universal one.
_SANDBOX_TOOLS = frozenset({"exec"})
# #419 entity tools — they read/write the item's workspace files (through the
# same `EntityStore` as the UI), so they need `function.workspace` just like the
# file tools.
_ENTITY_TOOLS = frozenset({"create_entity", "update_entity", "query_entity", "link_entity"})


def _subset_or_raise(
    declared: Iterable[str], ceiling: Iterable[str], *, kind: str, app: str, profile: str
) -> None:
    ceiling_set = set(ceiling)
    extra = [x for x in declared if x not in ceiling_set]
    if extra:
        raise ValueError(
            f"app {app!r} profile {profile!r}: {kind} {extra} not in the App "
            f"ceiling {sorted(ceiling_set)}"
        )


def _apply_tool_prefs(
    default_tools: Iterable[str],
    ceiling: Iterable[str],
    prefs: Mapping[str, bool] | None,
) -> list[str]:
    """Resolve the per-item tri-state tool override (#322) onto the default set.

    ``default_tools`` is the profile/App default; ``ceiling`` is the App's full
    ``tools`` (the override's hard upper bound). For each ceiling tool: a pref of
    ``True`` forces it ON, ``False`` forces it OFF, and an absent key follows the
    default. The result is emitted in ceiling order (deterministic); when there
    are no prefs the default set is returned verbatim (order preserved)."""
    if not prefs:
        return list(default_tools)
    default_set = set(default_tools)
    out: list[str] = []
    for name in ceiling:
        pinned = prefs.get(name)
        include = pinned if pinned is not None else name in default_set
        if include:
            out.append(name)
    return out


def validate_function_coherence(manifest: AppManifest) -> None:
    """Raise if the App's ``tools`` contradict its ``function`` toggles
    (decision 11). Called at catalog build / startup."""
    fn = manifest.function
    tools = set(manifest.agent.tools)
    if fn.terminal and not fn.sandbox:
        raise ValueError(f"app {manifest.slug!r}: function.terminal requires function.sandbox")
    if not fn.sandbox and (tools & _SANDBOX_TOOLS):
        raise ValueError(
            f"app {manifest.slug!r}: tools {sorted(tools & _SANDBOX_TOOLS)} need a "
            f"sandbox but function.sandbox is false"
        )
    if not fn.workspace and (tools & _FILE_TOOLS):
        raise ValueError(
            f"app {manifest.slug!r}: file tools {sorted(tools & _FILE_TOOLS)} need "
            f"function.workspace but it is false"
        )
    if not fn.workspace and (tools & _ENTITY_TOOLS):
        raise ValueError(
            f"app {manifest.slug!r}: entity tools {sorted(tools & _ENTITY_TOOLS)} need "
            f"function.workspace but it is false"
        )
    if manifest.layout.primary_surface in ("ide", "views") and not fn.workspace:
        raise ValueError(
            f"app {manifest.slug!r}: layout.primary_surface "
            f"{manifest.layout.primary_surface!r} requires function.workspace but it is false"
        )
    if manifest.layout.primary_surface == "views" and not manifest.layout.views:
        raise ValueError(
            f"app {manifest.slug!r}: layout.primary_surface 'views' needs a non-empty "
            f"layout.views (the main-screen view files)"
        )
    # #298 Q7: a declared shared skill must exist in the registry — a typo should
    # fail the boot loud, not silently drop the skill from the index.
    from .shared_skills import SHARED_SKILLS

    unknown = [s for s in manifest.agent.skills if s not in SHARED_SKILLS]
    if unknown:
        raise ValueError(
            f"app {manifest.slug!r}: agent.skills {unknown} not in the shared-skill "
            f"registry {sorted(SHARED_SKILLS)}"
        )


def discover_app_slugs() -> list[str]:
    """Every App's slug — a subdir of ``apps/`` that ships an ``app.json``.

    ``_``-prefixed dirs are skipped: they're internal, not user-facing Apps —
    e.g. ``_template`` (the copy-me scaffold) and ``__pycache__``."""
    root = resources.files(_APPS_PKG)
    return sorted(
        c.name
        for c in root.iterdir()
        if c.is_dir() and not c.name.startswith("_") and (c / "app.json").is_file()
    )


def validate_all_apps() -> None:
    """Run ``validate_function_coherence`` over every discovered App + the #100
    workflow-profile coherence check. Called at startup so an incoherent
    ``app.json`` (e.g. ``exec`` in tools but ``sandbox:false``) or a broken
    workflow ``run.py`` fails the boot loud (decision 11; manual §3/§12)."""
    from ..workflow.discovery import validate_workflow_profiles

    for slug in discover_app_slugs():
        validate_function_coherence(load_app_manifest(slug))
        validate_workflow_profiles(slug)


def _read_app_text(app_slug: str, rel: str) -> str:
    return (resources.files(_APPS_PKG) / app_slug / rel).read_text("utf-8")


def _read_base_preamble() -> str:
    """The shared workspace preamble (#241) — workspace awareness + guardrails
    prepended to every *workspace* App's prompt. A bundled `_`-prefixed file so
    `discover_app_slugs` never mistakes it for an App."""
    return (resources.files(_APPS_PKG) / "_base.md").read_text("utf-8")


def _read_sandbox_preamble() -> str:
    """The shared sandbox preamble — `exec` / shell / python-via-exec guidance
    prepended to every App whose `function.sandbox` is true. Split out from the
    workspace `_base` preamble so a workspace App with no sandbox (e.g. the
    `_template`) isn't told about an `exec` tool it doesn't have. A bundled
    `_`-prefixed file so `discover_app_slugs` never mistakes it for an App."""
    return (resources.files(_APPS_PKG) / "_sandbox.md").read_text("utf-8")


def _compose_prompt(
    base: str,
    appendix: str,
    skills: list[SkillMeta],
    *,
    preamble: str = "",
    sandbox_preamble: str = "",
) -> str:
    parts = [base.rstrip()] if base else []
    # #241: the shared workspace preamble sits after the App's identity (base)
    # and before the profile appendix. Empty for non-workspace Apps → omitted.
    if preamble:
        parts.append(preamble.rstrip())
    # The shared sandbox preamble follows the workspace preamble, still ahead of
    # the profile appendix. Empty for non-sandbox Apps → omitted.
    if sandbox_preamble:
        parts.append(sandbox_preamble.rstrip())
    if appendix:
        parts.append(appendix.rstrip())
    # §A skill index (#29 / #89): advertise the profile's `read_skill`-loadable
    # skills so the agent knows what's available without calling the tool first.
    if skills:
        lines = [
            "## Available skills",
            "",
            "Call `read_skill(name)` to load the body before applying one.",
            "",
        ]
        lines += [f"- `{m.name}`: {m.description}" for m in skills]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


class AppCatalog:
    def __init__(self, *, presets: Mapping[str, Preset]) -> None:
        self._presets = dict(presets)

    def resolve(
        self,
        *,
        app_slug: str,
        profile: str,
        attached_preset: str | None = None,
        tool_prefs: Mapping[str, bool] | None = None,
        skill_prefs: Mapping[str, bool] | None = None,
    ) -> AgentConfig:
        manifest = load_app_manifest(app_slug)
        prof = load_profile(app_slug, profile)

        # tools — profile subset of the App ceiling, else the whole ceiling.
        if prof.tools is not UNSET:
            _subset_or_raise(
                prof.tools, manifest.agent.tools, kind="tools", app=app_slug, profile=profile
            )
            default_tools = list(prof.tools)
        else:
            default_tools = list(manifest.agent.tools)
        # #322: a per-item tri-state override sits on top of that default. Each
        # entry pins one App-ceiling tool ON (True) or OFF (False); absent keys
        # follow the default (so future profile-default changes still flow). The
        # override ceiling is the App's `tools`, NOT the profile — a force-ON can
        # re-add a tool the profile narrowed away. Keys outside the ceiling no-op.
        tools = _apply_tool_prefs(default_tools, manifest.agent.tools, tool_prefs)
        # #480: the ceiling tools that resolved OFF. Surfaced to the agent as a
        # prompt-only "available on request" section (not registered callable),
        # so it knows they exist, avoids them by default, and can ask the user to
        # enable one. Ceiling order preserved; disjoint from `tools`.
        enabled = set(tools)
        disabled_tools = [t for t in manifest.agent.tools if t not in enabled]

        # allowed presets — profile subset of the App picker, else the whole picker.
        picker_presets = [p.preset for p in manifest.agent.picker]
        if prof.presets is not UNSET:
            _subset_or_raise(
                prof.presets, picker_presets, kind="presets", app=app_slug, profile=profile
            )
            allowed = list(prof.presets)
        else:
            allowed = list(picker_presets)

        chosen = (
            attached_preset
            if attached_preset in allowed
            else (prof.default_preset or (allowed[0] if allowed else ""))
        )
        if not chosen or chosen not in self._presets:
            raise ValueError(
                f"app {app_slug!r} profile {profile!r}: chosen preset {chosen!r} is "
                f"not declared in agents.presets {sorted(self._presets)}"
            )
        preset = self._presets[chosen]

        # skills — single source: `effective_item_skills` computes each available
        # skill's default_on (the profile's `skills` gates the declared shared set)
        # + effective (the per-item tri-state `skill_prefs` applied on top: True
        # forces ON even a default-off skill, False OFF, absent follows the
        # default). The prompt advertises only the effective ones. Workspace skills
        # are added per-turn (chat_send), so resolve passes none. The subset check
        # keeps a `skills` typo a loud config error, not a silently dropped skill.
        if prof.skills is not UNSET:
            _subset_or_raise(
                prof.skills, manifest.agent.skills, kind="skills", app=app_slug, profile=profile
            )
        skill_metas = [
            SkillMeta(name=s.name, description=s.description)
            for s in effective_item_skills(app_slug, profile, skill_prefs or {}, [])
            if s.effective
        ]

        system_prompt = _compose_prompt(
            _read_app_text(app_slug, manifest.agent.prompt_file),
            load_profile_appendix(app_slug, profile),
            skill_metas,
            preamble=_read_base_preamble() if manifest.function.workspace else "",
            sandbox_preamble=_read_sandbox_preamble() if manifest.function.sandbox else "",
        )
        suggestions = list(prof.suggestions or manifest.agent.suggestions)
        name = next((p.name for p in manifest.agent.picker if p.preset == chosen), chosen)

        return AgentConfig(
            name=name,
            model=preset.model,
            vision=preset.vision,
            system_prompt=system_prompt,
            description=preset.description,
            suggestions=suggestions,
            allowed_tools=tools,
            disabled_tools=disabled_tools,
            env=dict(preset.env),
            sandbox_image=preset.sandbox_image,
            idle_timeout_seconds=preset.idle_timeout_seconds,
            llm_base_url=preset.llm.base_url,
            llm_api_key=preset.llm.api_key,
        )
