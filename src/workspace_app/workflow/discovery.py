"""Load a profile's ``run()`` orchestration + validate workflow profiles (#100).

A workflow profile lives at ``apps/<slug>/profiles/<name>/`` with a ``workflow``
block in ``_profile.json`` (the manifest, discovered by ``apps.profiles``) **and** a
``run.py`` exposing ``async def run(wf, inputs)`` (the orchestration code, manual §3).

``run.py`` is loaded by **file path**, not ``import_module``: a profile dir name may
not be a valid Python identifier (e.g. ``smt-reflow-example``), so it isn't an
importable package. Its own imports are ordinary absolute imports of the workflow
library, so the loaded module's globals stay valid after load.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Awaitable, Callable
from importlib import resources
from pathlib import Path
from typing import Any

from ..apps.profiles import load_workflow_manifest, workflow_profiles
from .handle import WorkflowHandle
from .manifest import WorkflowManifest

_APPS_PKG = "workspace_app.apps"

ProfileRun = Callable[[WorkflowHandle, Any], Awaitable[Any]]


class WorkflowNotFound(LookupError):
    """The profile carries no runnable workflow — no ``workflow`` block, or no
    ``run.py`` / no ``run()`` callable in it."""


def _exec_run(path: Path, label: str) -> ProfileRun:
    """Exec a ``run.py`` at ``path`` (by file path, so a hyphenated profile dir
    works) and return its ``run`` callable. Raises ``WorkflowNotFound`` if there is
    no ``run`` callable."""
    mod_name = "_wf_" + re.sub(r"\W", "_", label)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None  # a real file always yields a loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    run = getattr(mod, "run", None)
    if not callable(run):
        raise WorkflowNotFound(f"{label}/run.py has no run() callable")
    return run


def load_run_callable(app_slug: str, profile: str) -> ProfileRun:
    """The profile's ``run`` coroutine, loaded from its ``run.py`` by file path.
    Raises ``WorkflowNotFound`` when the file or the ``run`` callable is missing."""
    run_path = resources.files(_APPS_PKG) / app_slug / "profiles" / profile / "run.py"
    if not run_path.is_file():
        raise WorkflowNotFound(f"{app_slug}/{profile} has no run.py")
    with resources.as_file(run_path) as p:
        return _exec_run(p, f"{app_slug}_{profile}")


def _check_phase_ids(manifest: WorkflowManifest, label: str) -> None:
    """Every declared phase needs a stable ``id`` (the diagram skeleton, manual §12)."""
    for phase in manifest.phases:
        if not phase.id:
            raise ValueError(
                f"{label}: a workflow phase is missing its 'id' "
                f"(manual §12 — the phase skeleton needs stable ids)"
            )


def validate_workflow_profiles(app_slug: str) -> None:
    """Fail loud at startup if any of the App's workflow profiles is incoherent:
    its ``run.py`` must import + expose ``run()``, and every declared phase must
    carry an ``id`` (manual §12)."""
    for profile in workflow_profiles(app_slug):
        load_run_callable(app_slug, profile)  # raises WorkflowNotFound on a bad run.py
        manifest = load_workflow_manifest(app_slug, profile)
        assert manifest is not None  # workflow_profiles only yields ones with a manifest
        _check_phase_ids(manifest, f"{app_slug}/{profile}")
