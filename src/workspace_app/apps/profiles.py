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
_TRIGGERS_FILE = "triggers.json"
_NON_PROFILE = {"__pycache__"}


class ProfileCollection(Struct):
    """#280: one entry in a profile's default collection set, declared by collection
    NAME (resilient across deployments — ids are specstar-generated and differ per
    deployment) plus an optional priority ``tier`` (sparse ints, e.g. 0/10/20, leave
    room to insert later). Resolved to a live id + seeded into the item's
    ``collections.json`` at creation; an unresolvable name is skipped (see
    ``kb.collections.resolve_profile_collections``)."""

    name: str
    tier: int = 0


class ProfileManifest(Struct):
    title: str = ""
    description: str = ""
    collections: list[ProfileCollection] = field(default_factory=list)
    """#280: the profile's DEFAULT collection set (by name + tier). Seeded into a new
    item's ``collections.json`` at creation, then editable in the web picker / Monaco.
    Empty (the default) ⇒ no default — the item starts with no scope (searches all)."""
    upload_dir: str = "uploads"
    """#198: the staging folder a chat attach lands in — ``{upload_dir}/<name>`` —
    and the default the profile's workflows glob (``wf.upload_dir``) / where their
    ``input_json`` lives (``{upload_dir}/input.json``). One source so attach and the
    workflows that consume the files never drift apart (the old hardcoded ``uploads/``
    in #234). Omitted ⇒ ``uploads``."""
    suggestions: list[Suggestion] = field(default_factory=list)
    tools: list[str] | UnsetType = UNSET  # ⊆ app.tools; UNSET → inherit all
    skills: list[str] | UnsetType = UNSET  # ⊆ app.skills; UNSET → inherit all (#380)
    presets: list[str] | UnsetType = UNSET  # ⊆ app.picker; UNSET → inherit all
    default_preset: str = ""
    workflows: list[WorkflowManifest] = field(default_factory=list)
    """#100 / manual §4: the profile's workflows, each addressed by a stable ``id``
    (``run.py`` at ``profiles/<name>/workflows/<id>/run.py``). One profile, N workflow
    types. Empty (the default) + no legacy ``workflow`` block → interactive-only."""
    workflow: WorkflowManifest | None = None
    """Legacy singular form (manual §4): one workflow, ``run.py`` at the profile root.
    Normalised to a one-element ``workflows`` list by ``normalize_workflows``; prefer
    the list. Mutually exclusive with ``workflows`` in practice."""


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
    """The profile's *legacy singular* workflow declaration, or ``None`` when the
    profile uses the new ``workflows`` list (or is interactive). Kept for the legacy
    run path (orchestrator / ``POST .../run``); new code uses ``profile_workflows``. #100."""
    return load_profile(app_slug, name).workflow


def normalize_workflows(pm: ProfileManifest) -> list[WorkflowManifest]:
    """A profile's workflows as a flat list (manual §4), pure (no IO). New form: the
    ``workflows`` list. Legacy form: the singular ``workflow`` block → a one-element
    list (its ``id`` stays ``""`` — the sentinel for the profile-root ``run.py``)."""
    if pm.workflows:
        return list(pm.workflows)
    if pm.workflow is not None:
        return [pm.workflow]
    return []


def profile_workflows(app_slug: str, name: str) -> list[WorkflowManifest]:
    """Every workflow the named profile declares (manual §4) — new list form or the
    normalised legacy singular. Empty for an interactive profile."""
    return normalize_workflows(load_profile(app_slug, name))


def load_profile_workflow(
    app_slug: str, name: str, workflow_id: str = ""
) -> WorkflowManifest | None:
    """The manifest of a SPECIFIC workflow in a profile (manual §4). With
    ``workflow_id`` → that entry from the ``workflows`` list (None if absent). Without
    → the legacy singular ``workflow``, else the first declared workflow (so a
    single-workflow profile resolves with no id). Backs the orchestrator's per-run
    manifest load + the run-route validation."""
    wfs = profile_workflows(app_slug, name)
    if workflow_id:
        return next((w for w in wfs if w.id == workflow_id), None)
    return wfs[0] if wfs else None


def workflow_profiles(app_slug: str) -> list[str]:
    """Names of the App's profiles that carry ≥1 workflow (manual §4, §14) — sorted."""
    return [p for p in list_profiles(app_slug) if profile_workflows(app_slug, p)]


def load_profile_appendix(app_slug: str, name: str) -> str:
    """The profile's ``_prompt.md`` system-prompt appendix, or "" if none."""
    try:
        return (_profiles_root(app_slug) / name / _PROMPT_FILE).read_text("utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError):
        return ""


def load_profile_triggers_raw(app_slug: str, name: str) -> bytes | None:
    """The raw bytes of the profile's ``triggers.json`` (#429 P6/P7), or ``None`` when it
    ships none. Parsing + validation live in ``workflow.triggers`` — this is only the
    resources boundary, symmetric with ``load_profile_appendix``."""
    try:
        return (_profiles_root(app_slug) / name / _TRIGGERS_FILE).read_bytes()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError):
        return None
