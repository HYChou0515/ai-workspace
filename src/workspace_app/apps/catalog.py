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
from .skills import SkillMeta, list_skills

if TYPE_CHECKING:
    from ..config.schema import Preset

_APPS_PKG = "workspace_app.apps"

# Builtin file tools — meaningful only when the App enables `workspace`.
_FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file", "ls", "exists", "delete_file"})
# Tools that need a compute sandbox. Package tools (data-fetch, …) also run in
# the sandbox, but their names are deploy-specific; `exec` is the universal one.
_SANDBOX_TOOLS = frozenset({"exec"})


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
    """Run ``validate_function_coherence`` over every discovered App. Called at
    startup so an incoherent ``app.json`` (e.g. ``exec`` in tools but
    ``sandbox:false``) fails the boot loud (decision 11)."""
    for slug in discover_app_slugs():
        validate_function_coherence(load_app_manifest(slug))


def _read_app_text(app_slug: str, rel: str) -> str:
    return (resources.files(_APPS_PKG) / app_slug / rel).read_text("utf-8")


def _compose_prompt(base: str, appendix: str, skills: list[SkillMeta]) -> str:
    parts = [base.rstrip()] if base else []
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
        self, *, app_slug: str, profile: str, attached_preset: str | None = None
    ) -> AgentConfig:
        manifest = load_app_manifest(app_slug)
        prof = load_profile(app_slug, profile)

        # tools — profile subset of the App ceiling, else the whole ceiling.
        if prof.tools is not UNSET:
            _subset_or_raise(
                prof.tools, manifest.agent.tools, kind="tools", app=app_slug, profile=profile
            )
            tools = list(prof.tools)
        else:
            tools = list(manifest.agent.tools)

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

        system_prompt = _compose_prompt(
            _read_app_text(app_slug, manifest.agent.prompt_file),
            load_profile_appendix(app_slug, profile),
            list_skills(app_slug, profile),
        )
        suggestions = list(prof.suggestions or manifest.agent.suggestions)
        name = next((p.name for p in manifest.agent.picker if p.preset == chosen), chosen)

        return AgentConfig(
            name=name,
            model=preset.model,
            system_prompt=system_prompt,
            description=preset.description,
            suggestions=suggestions,
            allowed_tools=tools,
            env=dict(preset.env),
            sandbox_image=preset.sandbox_image,
            idle_timeout_seconds=preset.idle_timeout_seconds,
            llm_base_url=preset.llm.base_url,
            llm_api_key=preset.llm.api_key,
        )
