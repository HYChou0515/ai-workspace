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
from workspace_app.workflow.offered import (
    offered_workflow_ids,
    resolve_offered_workflow,
    unparsable_workflow,
)

_DEF = json.dumps(
    {
        "id": "ignored",
        "title": "Mine",
        "phases": [{"id": "p"}],
        "steps": [{"type": "agent", "cache": True, "prompt": "hi", "phase": "p", "out": "o.md"}],
    }
).encode()


# A workflow file that will not parse: an agent step with no `cache` (required).
_BROKEN = b'{"id":"x","phases":[{"id":"p"}],"steps":[{"type":"agent","prompt":"hi","phase":"p"}]}'


def _files() -> tuple[WorkspaceFiles, str]:
    return WorkspaceFiles(MemoryFileStore()), "item-1"


def test_a_workflow_file_that_wont_parse_says_why_and_a_good_or_absent_one_says_nothing() -> None:
    """ONE criterion for "this file will not run" — the loader's own (`parse_def`
    raises) — asked the same way by the panel listing, the schedules route, the
    sweep and `save_schedules`, so all four say the same sentence."""
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/good.json", _DEF))
    asyncio.run(files.write(item, "/.workflows/broken.json", _BROKEN))

    problem = asyncio.run(unparsable_workflow(files.read, item, "broken"))

    assert problem is not None and "`cache` is required" in problem
    assert asyncio.run(unparsable_workflow(files.read, item, "good")) is None
    assert asyncio.run(unparsable_workflow(files.read, item, "not-there")) is None


def test_an_item_offers_its_profiles_workflows_and_its_own() -> None:
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/nightly.json", _DEF))
    asyncio.run(files.write(item, "/.workflows/nested/deep.json", _DEF))  # not flat → not offered
    asyncio.run(files.write(item, "/.workflows/notes.md", b"x"))  # not a workflow

    ids = asyncio.run(offered_workflow_ids(files.ls, item, slug="playground", profile="multi"))

    assert ids == ["alpha", "beta", "nightly"]


def test_an_interactive_profile_offers_only_what_the_item_authored() -> None:
    """The reported shape: `default` declares nothing, so before this the list
    was empty and every entrance but the panel refused the item's own work."""
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/nightly.json", _DEF))

    ids = asyncio.run(offered_workflow_ids(files.ls, item, slug="playground", profile="default"))

    assert ids == ["nightly"]


def test_the_schedules_file_is_not_a_workflow() -> None:
    """`.workflows/schedules.json` sits in the same folder and is `.json` too. It
    used to be listed as a workflow called `schedules`: save_schedules accepted
    `run: "schedules"`, the sweep's gate passed it and it failed deep inside
    `orchestrator.start`, while the panel's Run list (which parses) never showed
    it — the one list every entrance consults disagreed with the panel by
    construction."""
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/nightly.json", _DEF))
    asyncio.run(files.write(item, "/.workflows/schedules.json", b'{"schedules": []}'))

    ids = asyncio.run(offered_workflow_ids(files.ls, item, slug="playground", profile="default"))

    assert ids == ["nightly"]


def test_the_listing_source_is_the_callers_choice() -> None:
    """The rule is one; the SOURCE is not. A request answers from the facade
    (live sandbox first); the sweep must answer from the durable store, because
    the facade's warm-first probe is the recovery trigger on the hosted backend
    and would rebuild every reaped sandbox that has a schedule, once per tick.
    So the listing is handed in, and whatever answers `ls(item, prefix)` decides."""
    calls: list[tuple[str, str]] = []

    async def durable_ls(item_id: str, prefix: str = "") -> list[str]:
        calls.append((item_id, prefix))
        return [f"{prefix}nightly.json"]

    ids = asyncio.run(
        offered_workflow_ids(durable_ls, "item-1", slug="playground", profile="default")
    )

    assert ids == ["nightly"]
    assert calls == [("item-1", "/.workflows/")]


def test_an_item_of_no_app_offers_nothing() -> None:
    files, item = _files()
    asyncio.run(files.write(item, "/.workflows/nightly.json", _DEF))

    assert asyncio.run(offered_workflow_ids(files.ls, item, slug="", profile="")) == []
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
