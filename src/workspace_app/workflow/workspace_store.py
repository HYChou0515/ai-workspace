"""User-authored workflows saved in a workspace (#323, manual §22, Q5).

Mirrors the workspace-skill model (#298): a workflow the user co-designs with the AI
is saved as ``<workspace>/.workflows/<id>.json`` in the FileStore — item-local, live,
hand-editable, downloadable (the generic folder download), and dev-promotable. This
module owns that on-disk shape: validate a ``workflow.json``, write it (canonicalised
so the filename is the authoritative id), and list what's there for a panel / the Run
picker. The ``save_workflow`` agent tool is a thin wrapper over ``validate`` + ``save``.

Decoupled from the agent context so it unit-tests against a bare ``FileStore``.
"""

from __future__ import annotations

import re

import msgspec

from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from .dsl import DslError, WorkflowDef, build_manifest, parse_def, validate_def
from .manifest import WorkflowManifest

# Where a user+AI co-created workflow lives in a workspace — a dir distinct from the run
# journal ``/.workflow/<id>/`` (§9, #136), so a def never collides with a run's artifacts.
WORKSPACE_WORKFLOW_DIR = ".workflows"


def slugify_workflow_id(name: str) -> str:
    """A workflow id → kebab-case slug (lowercase; non-alphanumeric runs → a single
    ``-``; trimmed). The slug is the FILENAME (``.workflows/<slug>.json``) and the
    workflow's authoritative id, so the two never drift. ``""`` when nothing usable
    remains (the caller rejects)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def workspace_workflow_path(slug: str) -> str:
    return f"/{WORKSPACE_WORKFLOW_DIR}/{slug}.json"


def validate_workflow_json(
    raw: bytes | str, *, tool_ceiling: set[str] | None = None
) -> tuple[WorkflowDef | None, list[str]]:
    """Parse + statically validate a ``workflow.json``. Returns ``(def, [])`` when it's
    runnable, or ``(None, [problems])`` when it won't parse / ``(def, [problems])`` when
    it parses but the DSL rules flag it (manual §22, Q8). ``tool_ceiling`` clamps an agent
    step's ``tools`` to the profile's allowed set (``None`` ⇒ skip that check)."""
    try:
        d = parse_def(raw)
    except DslError as exc:
        return None, [f"workflow.json won't parse: {exc}"]
    return d, validate_def(d, tool_ceiling=tool_ceiling)


async def load_workspace_workflow(
    files: WorkspaceFiles, workspace_id: str, workflow_id: str
) -> tuple[WorkflowDef, WorkflowManifest] | None:
    """A workspace ``.workflows/<workflow_id>.json`` parsed into ``(def, manifest)`` (the
    manifest id forced to the addressing ``workflow_id`` — the filename is authoritative),
    or ``None`` when absent / malformed. The single read backing both the orchestrator's
    run resolution and the route's manifest 404 guard (#323 P4)."""
    if not workflow_id:
        return None
    try:
        d = parse_def(await files.read(workspace_id, workspace_workflow_path(workflow_id)))
    except (FileNotFound, DslError):
        return None
    return d, msgspec.structs.replace(build_manifest(d), id=workflow_id)


async def save_workspace_workflow(
    files: WorkspaceFiles, workspace_id: str, slug: str, d: WorkflowDef
) -> str:
    """Write ``d`` to ``.workflows/<slug>.json`` (canonical msgspec JSON, ``id`` forced to
    ``slug`` so the filename is authoritative). Returns the workspace path. Re-saving the
    same slug overwrites (refine freely)."""
    path = workspace_workflow_path(slug)
    await files.write(workspace_id, path, msgspec.json.encode(msgspec.structs.replace(d, id=slug)))
    return path


async def workspace_workflow_metas(
    files: WorkspaceFiles, workspace_id: str
) -> list[WorkflowManifest]:
    """The manifest (id + title + phases) of every well-formed workflow under the
    workspace's ``.workflows/`` dir, sorted by id, the filename winning as the id. A
    malformed one is skipped (not surfaced here — ``save_workflow`` is the loud guard),
    so one bad hand-edit can't break the whole list. Empty when there's none."""
    prefix = f"/{WORKSPACE_WORKFLOW_DIR}/"
    out: list[WorkflowManifest] = []
    for path in sorted(await files.ls(workspace_id, prefix)):
        rel = path[len(prefix) :]
        if "/" in rel or not rel.endswith(".json"):
            continue  # only flat .workflows/<id>.json (no nested dirs)
        try:
            d = parse_def(await files.read(workspace_id, path))
        except (DslError, FileNotFound):
            continue
        out.append(msgspec.structs.replace(build_manifest(d), id=rel[: -len(".json")]))
    return out
