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


async def test_listing_cost_does_not_scale_with_how_many_workflows() -> None:
    """Listing the panel's workflows is ONE operation, so it settles where the
    workspace lives once instead of once per workflow file.

    Same defect the entity listings had: against the hosted sandbox each
    resolution is a network round trip, so a workspace with 30 workflows paid 31
    of them to read 30 files."""
    from tests.warm_workspace import warm_files

    async def probes_for(count: int) -> int:
        files, sb = await warm_files()
        for i in range(count):
            await files.write("ws1", f"/.workflows/w{i}.json", _VALID.encode())
        sb.liveness_probes = 0
        metas = await workspace_workflow_metas(files, "ws1")
        assert len(metas) == count
        return sb.liveness_probes

    few, many = await probes_for(2), await probes_for(20)
    assert many == few, (
        f"{few} probes for 2 workflows but {many} for 20 — the cost of locating "
        "the workspace is scaling with how many workflows it holds"
    )


async def test_a_workflow_that_vanishes_after_the_listing_does_not_break_the_panel() -> None:
    """`ls` names the files, and reading them happens after — so a file deleted
    in between is a RACE, not a corrupt workspace, and the rest of the panel
    still has to render. (`list_item_workflows` has no handler for it, so the
    unguarded version does not render an empty panel — it 500s the route.)

    Reading them one at a time made this free (the loop skipped a `FileNotFound`
    and carried on). Batching them is what puts the whole listing at risk of one
    vanished file, so the tolerance has to be stated rather than inherited."""

    class _GhostListing(WorkspaceFiles):
        """A listing that names a file the reads can no longer find — the race,
        as the store sees it."""

        async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
            got = await super().ls(workspace_id, prefix)
            return [*got, "/.workflows/ghost.json"]  # listed, but never written

    files = _GhostListing(MemoryFileStore())
    for name in ("a", "b"):
        await files.write("ws1", f"/.workflows/{name}.json", _VALID.encode())

    metas = await workspace_workflow_metas(files, "ws1")

    assert [m.id for m in metas] == ["a", "b"]
