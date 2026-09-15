"""`schedules` is not a workflow id — at EVERY door, not only the tool's.

The read side (`is_workspace_workflow_path`, `offered_workflow_ids`) reserves
the name; a write side that still handed it out let `save_workflow("Schedules")`
overwrite the item's schedules file. The chokepoint every writer shares is
`save_workspace_workflow`; the tool answers first with a sentence, and the
template-copy route must turn the refusal into a 422, not a 500.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from specstar import SpecStar

from tests.api._client import TestClient
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow import shared
from workspace_app.workflow.dsl import parse_def
from workspace_app.workflow.offered import resolve_offered_workflow
from workspace_app.workflow.workspace_store import (
    RESERVED_WORKFLOW_ID,
    ReservedWorkflowId,
    is_workspace_workflow_path,
    save_workspace_workflow,
)

_DEF = json.dumps(
    {
        "id": "ignored",
        "title": "T",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
)


def test_the_write_chokepoint_refuses_the_reserved_id() -> None:
    """Every writer goes through here; the tool's own pre-check is a courtesy."""
    files, item = WorkspaceFiles(MemoryFileStore()), "item-1"
    asyncio.run(files.write(item, "/.workflows/schedules.json", b'{"schedules": []}'))

    with pytest.raises(ReservedWorkflowId):
        asyncio.run(save_workspace_workflow(files, item, RESERVED_WORKFLOW_ID, parse_def(_DEF)))

    kept = asyncio.run(files.read(item, "/.workflows/schedules.json"))
    assert json.loads(kept) == {"schedules": []}, "the schedules file was overwritten"


def test_a_template_named_like_the_schedules_file_is_refused_not_a_500(monkeypatch) -> None:
    """A deployment replaces `SHARED_WORKFLOWS` with its own dict; one that names
    a template `schedules` must get a refusal it can read, not a traceback."""
    monkeypatch.setitem(
        shared.SHARED_WORKFLOWS,
        RESERVED_WORKFLOW_ID,
        Path(__file__).resolve().parents[2] / "sample-workflows" / "image-to-knowledge",
    )
    spec: SpecStar = make_spec()
    app: FastAPI = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()]),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="dsl"))
        .resource_id
    )
    with TestClient(app) as client:
        r = client.post(f"/a/playground/items/{item_id}/workflow-templates/schedules/copy")

    assert r.status_code == 422, r.text
    assert "schedules" in r.json()["detail"]


def test_the_loader_every_reader_shares_refuses_the_reserved_id() -> None:
    """The orchestrator, the run route and the panel's resolver all load a
    workspace workflow through `load_workspace_workflow`. Refused there, a
    workflow body written into the schedules file is a workflow to nobody —
    including a dev-authored trigger naming it by id."""
    from workspace_app.workflow.workspace_store import load_workspace_workflow

    files, item = WorkspaceFiles(MemoryFileStore()), "item-1"
    asyncio.run(files.write(item, "/.workflows/schedules.json", _DEF.encode()))

    assert asyncio.run(load_workspace_workflow(files, item, RESERVED_WORKFLOW_ID)) is None


def test_the_manifest_resolver_does_not_hand_out_a_workflow_by_the_reserved_id() -> None:
    """A workflow BODY written to `.workflows/schedules.json` by hand parses as a
    workflow. The id list excludes it; the manifest resolver — what the panel's
    Run and `POST …/runs` go through — must agree, or the two disagree by
    construction, which is the thing the shared rule exists to prevent."""
    files, item = WorkspaceFiles(MemoryFileStore()), "item-1"
    asyncio.run(files.write(item, "/.workflows/schedules.json", _DEF.encode()))

    got = asyncio.run(
        resolve_offered_workflow(
            files, item, slug="playground", profile="default", workflow_id="schedules"
        )
    )

    assert got is None


def test_the_path_predicate_answers_for_paths_outside_the_folder_too() -> None:
    """Callers pre-filter by prefix today; the predicate must not depend on it."""
    assert is_workspace_workflow_path("/.workflows/x.json")
    assert not is_workspace_workflow_path("/notes/x.json")
    assert not is_workspace_workflow_path("/.workflows/schedules.json")
    assert not is_workspace_workflow_path("/.workflows/nested/x.json")
