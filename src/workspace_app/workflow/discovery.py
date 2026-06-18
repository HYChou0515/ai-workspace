"""Load a profile's ``run()`` orchestration + validate workflow profiles (#100).

A workflow profile lives at ``apps/<slug>/profiles/<name>/`` and declares its
workflows in ``_profile.json`` (manual §4) — either the new ``workflows: [...]`` list
(each entry's ``run.py`` at ``profiles/<name>/workflows/<id>/run.py``) or the legacy
singular ``workflow`` block (one ``run.py`` at the profile root). Each ``run.py``
exposes ``async def run(wf, inputs)`` (the orchestration code, manual §3).

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

from ..apps.profiles import load_profile, workflow_profiles
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


def load_run_callable(app_slug: str, profile: str, workflow_id: str = "") -> ProfileRun:
    """A workflow's ``run`` coroutine, loaded from its ``run.py`` by file path.

    ``workflow_id`` selects the new list-form layout
    (``profiles/<profile>/workflows/<workflow_id>/run.py``); the default ``""`` is the
    legacy singular layout (``run.py`` at the profile root). Raises ``WorkflowNotFound``
    when the file or the ``run`` callable is missing."""
    base = resources.files(_APPS_PKG) / app_slug / "profiles" / profile
    run_path = base / "workflows" / workflow_id / "run.py" if workflow_id else base / "run.py"
    if not run_path.is_file():
        where = f"workflows/{workflow_id}/run.py" if workflow_id else "run.py"
        raise WorkflowNotFound(f"{app_slug}/{profile} has no {where}")
    label = f"{app_slug}_{profile}" + (f"_{workflow_id}" if workflow_id else "")
    with resources.as_file(run_path) as p:
        return _exec_run(p, label)


def _check_phase_ids(manifest: WorkflowManifest, label: str) -> None:
    """Every declared phase needs a stable ``id`` (the diagram skeleton, manual §12)."""
    for phase in manifest.phases:
        if not phase.id:
            raise ValueError(
                f"{label}: a workflow phase is missing its 'id' "
                f"(manual §12 — the phase skeleton needs stable ids)"
            )


def _check_workflow_ids(workflows: list[WorkflowManifest], label: str) -> None:
    """Every workflow in the list form needs a non-empty, unique ``id`` — it is how a
    workflow is addressed (run.py path + the new-chat picker, manual §4)."""
    seen: set[str] = set()
    for wf in workflows:
        if not wf.id:
            raise ValueError(
                f"{label}: a workflow in 'workflows' is missing its 'id' "
                f"(manual §4 — each workflow is addressed by a stable id)"
            )
        if wf.id in seen:
            raise ValueError(f"{label}: duplicate workflow id {wf.id!r} (manual §4)")
        seen.add(wf.id)


def validate_workflow_profiles(app_slug: str) -> None:
    """Fail loud at startup if any of the App's workflow profiles is incoherent: every
    declared workflow's ``run.py`` must import + expose ``run()``, list-form workflows
    need unique non-empty ids (manual §4), and every phase needs an ``id`` (§12)."""
    for profile in workflow_profiles(app_slug):
        pm = load_profile(app_slug, profile)
        if pm.workflows:  # new list form (manual §4)
            _check_workflow_ids(pm.workflows, f"{app_slug}/{profile}")
            for wf in pm.workflows:
                load_run_callable(app_slug, profile, wf.id)  # raises on a bad run.py
                _check_phase_ids(wf, f"{app_slug}/{profile}/{wf.id}")
        else:  # legacy singular form — run.py at the profile root
            assert pm.workflow is not None  # workflow_profiles guarantees ≥1 workflow
            load_run_callable(app_slug, profile)
            _check_phase_ids(pm.workflow, f"{app_slug}/{profile}")
