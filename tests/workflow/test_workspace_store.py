"""Workspace-authored workflows (#323, manual §22): validate a workflow.json, write it
canonicalised, and list what a workspace holds (the panel / Run-picker data source)."""

from __future__ import annotations

import json

from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import parse_def
from workspace_app.workflow.workspace_store import (
    load_workspace_workflow,
    save_workspace_workflow,
    slugify_workflow_id,
    validate_workflow_json,
    workspace_workflow_metas,
    workspace_workflow_path,
)

_VALID = json.dumps(
    {
        "id": "ignored",
        "title": "T",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
)


def _files() -> tuple[WorkspaceFiles, str]:
    return WorkspaceFiles(MemoryFileStore()), "ws"


def test_slugify_and_path():
    assert slugify_workflow_id("My Cool Flow!") == "my-cool-flow"
    assert slugify_workflow_id("!!!") == ""
    assert workspace_workflow_path("x") == "/.workflows/x.json"


def test_validate_ok_parsefail_and_invalid():
    d, errs = validate_workflow_json(_VALID)
    assert d is not None and errs == []
    none, perrs = validate_workflow_json("{not json")
    assert none is None and "won't parse" in perrs[0]
    bad = json.dumps(
        {
            "id": "x",
            "phases": [{"id": "p"}],
            "steps": [{"type": "sandbox", "run": "x", "phase": "zz"}],
        }
    )
    d2, verrs = validate_workflow_json(bad)
    assert d2 is not None and any("not declared" in e for e in verrs)


def test_validate_tool_ceiling_clamps():
    over = json.dumps(
        {
            "id": "x",
            "phases": [{"id": "p"}],
            "steps": [
                {"type": "agent", "prompt": "p", "phase": "p", "out": "o", "tools": ["exec"]}
            ],
        }
    )
    _d, errs = validate_workflow_json(over, tool_ceiling={"read_file"})
    assert any("tool 'exec' is outside" in e for e in errs)


async def test_save_forces_id_to_slug_and_canonicalises():
    files, ws = _files()
    d, _errs = validate_workflow_json(_VALID)
    assert d is not None
    path = await save_workspace_workflow(files, ws, "my-flow", d)
    assert path == "/.workflows/my-flow.json"
    saved = parse_def(await files.read(ws, path))
    assert saved.id == "my-flow"  # the slug wins over the json's own id


async def test_metas_lists_sorted_skips_malformed_and_nested():
    files, ws = _files()
    await files.write(ws, "/.workflows/zeta.json", _VALID.encode())
    await files.write(ws, "/.workflows/alpha.json", _VALID.encode())
    await files.write(ws, "/.workflows/broken.json", b"{not json")
    await files.write(ws, "/.workflows/nested/deep.json", _VALID.encode())  # nested → skipped
    metas = await workspace_workflow_metas(files, ws)
    assert [m.id for m in metas] == ["alpha", "zeta"]  # filename is the id; broken + nested gone
    assert metas[0].title == "T"


async def test_load_workspace_workflow_resolves_or_none():
    files, ws = _files()
    assert await load_workspace_workflow(files, ws, "") is None  # empty id
    assert await load_workspace_workflow(files, ws, "missing") is None  # absent file
    await files.write(ws, "/.workflows/broken.json", b"{not json")
    assert await load_workspace_workflow(files, ws, "broken") is None  # malformed → None
    await files.write(ws, "/.workflows/good.json", _VALID.encode())
    res = await load_workspace_workflow(files, ws, "good")
    assert res is not None
    _d, manifest = res
    assert manifest.id == "good"  # forced to the addressing id (filename authoritative)
    assert manifest.title == "T"
