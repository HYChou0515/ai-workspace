"""`workflow.offered` — the one rule for "which workflows may this item start".

The rule: the profile's workflows plus the item's own `.workflows/<id>.json`, a
workspace one shadowing a package one of the same id. Every entrance (panel,
page, schedule) consults this; `tests/api/test_offered_workflows.py` proves the
wiring, this file pins the rule.
"""

from __future__ import annotations

import asyncio
import json

from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.offered import offered_workflow_ids, resolve_offered_workflow

_DEF = json.dumps(
    {
        "id": "ignored",
        "title": "Mine",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
).encode()


def _files() -> tuple[WorkspaceFiles, str]:
    return WorkspaceFiles(MemoryFileStore()), "item-1"


def test_an_item_offers_its_profiles_workflows_and_its_own() -> None:
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/nightly.json", _DEF))
    asyncio.run(files.write(item, "/.workflows/nested/deep.json", _DEF))  # not flat → not offered
    asyncio.run(files.write(item, "/.workflows/notes.md", b"x"))  # not a workflow

    ids = asyncio.run(offered_workflow_ids(files, item, slug="playground", profile="multi"))

    assert ids == ["alpha", "beta", "nightly"]


def test_an_interactive_profile_offers_only_what_the_item_authored() -> None:
    """The reported shape: `default` declares nothing, so before this the list
    was empty and every entrance but the panel refused the item's own work."""
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/nightly.json", _DEF))

    ids = asyncio.run(offered_workflow_ids(files, item, slug="playground", profile="default"))

    assert ids == ["nightly"]


def test_an_item_of_no_app_offers_nothing() -> None:
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/nightly.json", _DEF))

    assert asyncio.run(offered_workflow_ids(files, item, slug="", profile="")) == []
    assert (
        asyncio.run(
            resolve_offered_workflow(files, item, slug="", profile="", workflow_id="nightly")
        )
        is None
    )


def test_a_workspace_workflow_shadows_the_package_one_of_the_same_id() -> None:
    """The order the orchestrator RUNS them in. Handing out the package manifest
    for an id the workspace overrides would describe phases that never execute."""
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/alpha.json", _DEF))

    got = asyncio.run(
        resolve_offered_workflow(
            files, item, slug="playground", profile="multi", workflow_id="alpha"
        )
    )

    assert got is not None
    assert (got.id, got.title) == ("alpha", "Mine")


def test_a_package_workflow_resolves_when_the_item_has_no_override() -> None:
    files, item = _files()

    got = asyncio.run(
        resolve_offered_workflow(
            files, item, slug="playground", profile="multi", workflow_id="beta"
        )
    )

    assert got is not None and got.id == "beta"


def test_an_unknown_id_resolves_to_nothing() -> None:
    files, item = _files()

    assert (
        asyncio.run(
            resolve_offered_workflow(
                files, item, slug="playground", profile="multi", workflow_id="nope"
            )
        )
        is None
    )


def test_an_empty_id_still_means_the_profiles_default_workflow() -> None:
    """A single-workflow profile (`echo`) runs without naming its workflow — the
    run route has always allowed that, and the shared rule must not take it away."""
    files, item = _files()

    got = asyncio.run(
        resolve_offered_workflow(files, item, slug="playground", profile="echo", workflow_id="")
    )

    assert got is not None
