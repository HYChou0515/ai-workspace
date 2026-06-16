"""Profiles — an App's named starter-content bundles (#89).

A profile lives at ``apps/<slug>/profiles/<name>/`` and contributes: starter
files (seeded on item-create), a ``_prompt.md`` prompt appendix, a ``.skill/``
dir, and an optional ``_profile.json`` that **narrows** the App's agent ceiling
to a subset (``tools`` ⊆ app.tools, ``presets`` ⊆ app.picker) plus per-profile
``suggestions`` / ``default_preset`` / display strings. Omitted ``_profile.json``
(or omitted fields) → inherit the App's ceiling (``UNSET``).
"""

from __future__ import annotations

from importlib import resources

import msgspec
from msgspec import UNSET, Struct, UnsetType, field

from ..resources.agent_config import Suggestion
from ..workflow.manifest import WorkflowManifest

_APPS_PKG = "workspace_app.apps"
_PROFILES_DIR = "profiles"
_PROFILE_FILE = "_profile.json"
_PROMPT_FILE = "_prompt.md"
_NON_PROFILE = {"__pycache__"}


class ProfileManifest(Struct):
    title: str = ""
    description: str = ""
    suggestions: list[Suggestion] = field(default_factory=list)
    tools: list[str] | UnsetType = UNSET  # ⊆ app.tools; UNSET → inherit all
    presets: list[str] | UnsetType = UNSET  # ⊆ app.picker; UNSET → inherit all
    default_preset: str = ""
    workflow: WorkflowManifest | None = None
    """#100: present → this profile is a headless-triggerable workflow (manual §14).
    Absent (the default) → an ordinary interactive profile."""


def _profiles_root(app_slug: str):
    return resources.files(_APPS_PKG) / app_slug / _PROFILES_DIR


def list_profiles(app_slug: str) -> list[str]:
    """Names of the App's profiles (subdirs of ``profiles/``), sorted."""
    try:
        children = list(_profiles_root(app_slug).iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted(c.name for c in children if c.is_dir() and c.name not in _NON_PROFILE)


def load_profile(app_slug: str, name: str) -> ProfileManifest:
    """The profile's declared overrides (``_profile.json``), or all-defaults
    (inherit the App ceiling) when it ships none."""
    try:
        raw = (_profiles_root(app_slug) / name / _PROFILE_FILE).read_bytes()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError):
        return ProfileManifest()
    return msgspec.json.decode(raw, type=ProfileManifest)


def load_workflow_manifest(app_slug: str, name: str) -> WorkflowManifest | None:
    """The profile's workflow declaration, or ``None`` if it is an ordinary
    interactive profile (no ``workflow`` block in ``_profile.json``). #100."""
    return load_profile(app_slug, name).workflow


def workflow_profiles(app_slug: str) -> list[str]:
    """Names of the App's profiles that carry a workflow (manual §14) — sorted."""
    return [p for p in list_profiles(app_slug) if load_workflow_manifest(app_slug, p) is not None]


def load_profile_appendix(app_slug: str, name: str) -> str:
    """The profile's ``_prompt.md`` system-prompt appendix, or "" if none."""
    try:
        return (_profiles_root(app_slug) / name / _PROMPT_FILE).read_text("utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError):
        return ""
