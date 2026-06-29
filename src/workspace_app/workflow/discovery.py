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
from .dsl import build_run, parse_def
from .handle import WorkflowHandle
from .manifest import WorkflowManifest
from .preflight import Preflight

_APPS_PKG = "workspace_app.apps"

ProfileRun = Callable[[WorkflowHandle, Any], Awaitable[Any]]


class WorkflowNotFound(LookupError):
    """The profile carries no runnable workflow — no ``workflow`` block, or no
    ``run.py`` / no ``run()`` callable in it."""


def _exec_module(path: Path, label: str):
    """Exec a ``run.py`` at ``path`` (by file path, so a hyphenated profile dir works)
    and return the loaded module. Its functions (``run``, optional ``preflight``) are
    pulled off by the callers below."""
    mod_name = "_wf_" + re.sub(r"\W", "_", label)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None  # a real file always yields a loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exec_run(path: Path, label: str) -> ProfileRun:
    """Exec a ``run.py`` and return its ``run`` callable. Raises ``WorkflowNotFound``
    if there is no ``run`` callable."""
    run = getattr(_exec_module(path, label), "run", None)
    if not callable(run):
        raise WorkflowNotFound(f"{label}/run.py has no run() callable")
    return run


def _run_py_path(app_slug: str, profile: str, workflow_id: str):
    """The ``run.py`` traversable for a workflow: the list-form
    ``profiles/<profile>/workflows/<workflow_id>/run.py`` when ``workflow_id`` is set,
    else the legacy profile-root ``run.py``."""
    base = resources.files(_APPS_PKG) / app_slug / "profiles" / profile
    return base / "workflows" / workflow_id / "run.py" if workflow_id else base / "run.py"


def _workflow_json_path(app_slug: str, profile: str, workflow_id: str):
    """The ``workflow.json`` traversable for a list-form DSL workflow (#323, manual §22):
    ``profiles/<profile>/workflows/<workflow_id>/workflow.json``. A DSL workflow is
    declared in ``_profile.json`` like any other (its id addresses this dir); the trusted
    interpreter runs the data in place of a ``run.py`` — the same interpreter that serves a
    workspace-authored one (Q6). Only the list form (a non-empty ``workflow_id``)."""
    base = resources.files(_APPS_PKG) / app_slug / "profiles" / profile
    return base / "workflows" / workflow_id / "workflow.json"


def _run_py_label(app_slug: str, profile: str, workflow_id: str) -> str:
    return f"{app_slug}_{profile}" + (f"_{workflow_id}" if workflow_id else "")


def load_run_callable(app_slug: str, profile: str, workflow_id: str = "") -> ProfileRun:
    """A workflow's ``run`` coroutine. A list-form workflow may be authored as **data** —
    a ``workflow.json`` (#323, manual §22) the trusted interpreter runs — or as Python
    (``run.py`` loaded by file path); the JSON wins when both are present (Q6). The legacy
    singular layout (``workflow_id=""``) is ``run.py`` at the profile root. Raises
    ``WorkflowNotFound`` when neither the JSON nor a ``run()`` callable is there."""
    if workflow_id:
        json_path = _workflow_json_path(app_slug, profile, workflow_id)
        if json_path.is_file():
            with resources.as_file(json_path) as p:
                return build_run(parse_def(p.read_bytes()))
    run_path = _run_py_path(app_slug, profile, workflow_id)
    if not run_path.is_file():
        where = f"workflows/{workflow_id}/run.py" if workflow_id else "run.py"
        raise WorkflowNotFound(f"{app_slug}/{profile} has no {where}")
    with resources.as_file(run_path) as p:
        return _exec_run(p, _run_py_label(app_slug, profile, workflow_id))


def load_preflight_callable(app_slug: str, profile: str, workflow_id: str = "") -> Preflight | None:
    """A workflow's optional ``preflight`` coroutine (#283), loaded from the same
    ``run.py`` as ``run``. Returns ``None`` when the file or the ``preflight`` callable
    is absent — pre-flight is opt-in, so the launch dialog falls back to the workflow's
    phases. Never raises (an absent hook is normal, not an error)."""
    run_path = _run_py_path(app_slug, profile, workflow_id)
    if not run_path.is_file():
        return None
    with resources.as_file(run_path) as p:
        pf = getattr(
            _exec_module(p, _run_py_label(app_slug, profile, workflow_id)), "preflight", None
        )
    return pf if callable(pf) else None


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
