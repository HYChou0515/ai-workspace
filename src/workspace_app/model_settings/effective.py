"""The settings everything runs with (docs/plan-model-settings.md P3).

The deploy defaults — config.yaml over the bundled settings, each App's
app.json, each profile's _profile.json — with the page's published patch and
keys over them. The page edits one document:

    {"config":   {...the config.yaml tree...},
     "apps":     {"<slug>": {...app.json...}},
     "profiles": {"<slug>": {"<profile>": {..._profile.json...}}}}

each part a JSON Merge Patch (M11, M12). Every function here takes the document
it is given and reads nothing published, so the page can check a draft and a
consumer can build from the published one with the same code (M13).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import msgspec

from ..apps.catalog import _subset_or_raise, discover_app_slugs, validate_function_coherence
from ..apps.manifest import AppManifest, patched_app
from ..apps.profiles import ProfileManifest, list_profiles, patched_profile
from ..config.loader import load_with_provenance
from ..config.schema import Settings


def with_keys(document: dict[str, Any], keys: Mapping[str, str]) -> dict[str, Any]:
    """`document` with each key's value written at its dotted path — a copy;
    keys live apart from the patch so history never holds them (M6)."""
    out = copy.deepcopy(document)
    for path, value in keys.items():
        *parents, leaf = path.split(".")
        node = out
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def effective_settings(
    document: dict[str, Any], *, config_path: Path | None, env: Mapping[str, str]
) -> Settings:
    """The typed settings with the document's `config` part applied — through
    the loader, so every check a boot runs runs here too (M8)."""
    settings, _ = load_with_provenance(
        config_path=config_path, env=env, overlay=document.get("config") or None
    )
    return settings


def effective_app(slug: str, document: dict[str, Any]) -> AppManifest:
    return patched_app(slug, document)


def effective_profile(slug: str, name: str, document: dict[str, Any]) -> ProfileManifest:
    return patched_profile(slug, name, document)


def problems(
    document: dict[str, Any], *, config_path: Path | None, env: Mapping[str, str]
) -> list[str]:
    """What stops `document` from running — empty when it can. Apply refuses
    on any (M8). Structure only: a model name that exists nowhere passes, which
    is what the test-connection button is for.

    The checks are the platform's own — the loader's, `validate_function_coherence`,
    the profile ⊂ picker subset check — plus one boot never ran: every preset a
    picker or profile names exists, which today is found only when a turn
    resolves it."""
    try:
        settings = effective_settings(document, config_path=config_path, env=env)
    except (ValueError, TypeError) as exc:
        return [f"config: {exc}"]
    presets = set(settings.agents.presets)
    found: list[str] = []
    for slug in discover_app_slugs():
        try:
            app = effective_app(slug, document)
            validate_function_coherence(app)
        except (msgspec.ValidationError, ValueError) as exc:
            found.append(f"apps.{slug}: {exc}")
            continue
        picker = [entry.preset for entry in app.agent.picker]
        found += [
            f"apps.{slug}.agent.picker: preset {name!r} is not in agents.presets"
            for name in picker
            if name not in presets
        ]
        for profile in _profiles(slug, document):
            where = f"profiles.{slug}.{profile}"
            try:
                prof = effective_profile(slug, profile, document)
            except msgspec.ValidationError as exc:
                found.append(f"{where}: {exc}")
                continue
            if prof.presets is not msgspec.UNSET:
                try:
                    _subset_or_raise(
                        prof.presets, picker, kind="presets", app=slug, profile=profile
                    )
                except ValueError as exc:
                    found.append(f"{where}: {exc}")
            if prof.default_preset and prof.default_preset not in presets:
                found.append(
                    f"{where}.default_preset: preset {prof.default_preset!r} "
                    "is not in agents.presets"
                )
    return found


def _profiles(slug: str, document: dict[str, Any]) -> list[str]:
    """The App's shipped profiles, and any the document names."""
    named = ((document.get("profiles") or {}).get(slug) or {}).keys()
    return sorted(set(list_profiles(slug)) | set(named))
