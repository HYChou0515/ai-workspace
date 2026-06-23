"""Characterization tests filling coverage gaps in the workflow package.

Covers:

- ``steps.agent_write_step`` missing-driver guard (steps.py 98),
- ``WorkflowOrchestrator._post_run`` non-terminal/non-awaiting branch
  (orchestrator.py 350->352),
- the playground ``multi/beta`` fixture ``run()`` (beta/run.py 11),
- defensive / edge branches in the topic-hub ``→collections`` workflow run.py
  (49-50, 52->58, 54->53, 56->53, 76->72, 91, 155->154, 162->159).
"""

from __future__ import annotations

import pytest
from specstar import SpecStar

from workspace_app.apps.profiles import _profiles_root
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.workflow.discovery import load_run_callable
from workspace_app.workflow.gate import AwaitingHuman, record_decision
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.orchestrator import WorkflowOrchestrator
from workspace_app.workflow.run import RunStatus, WorkflowRun
from workspace_app.workflow.steps import agent_write_step


@pytest.fixture
def spec_instance() -> SpecStar:
    return make_spec(default_user="test-user")


# ─── steps.agent_write_step missing-driver guard (steps.py 98) ─────────────


async def test_agent_write_step_without_a_turn_driver_raises():
    """`agent_write_step` needs a wired `drive_turn`; absent it raises a clear
    RuntimeError (line 98) instead of an AttributeError later."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")  # no drive_turn
    with pytest.raises(RuntimeError, match="agent_write_step needs a turn driver"):
        await agent_write_step(wf, prompt="p", phase="ph", out="out.md")


# ─── orchestrator._post_run non-terminal/non-awaiting branch (350->352) ────


async def test_post_run_on_a_still_running_run_releases_nothing(spec_instance: SpecStar):
    """If `_post_run` ever sees a run that is neither terminal nor awaiting a
    human (here forced to RUNNING), it skips the release/revoke/notify entirely
    (branch 350->352)."""
    released: list = []

    async def release(item_id, terminal, key):
        released.append((item_id, terminal, key))

    async def _never_run(wf, inputs):  # load_run target — never invoked here
        return {}

    orch = WorkflowOrchestrator(
        spec=spec_instance,
        store=MemoryFileStore(),
        load_run=lambda _s, _p, _w="": _never_run,
        load_manifest=lambda _s, _p, _w="": None,
        wire_handle=lambda *_a: None,
        release=release,
    )
    rm = spec_instance.get_resource_manager(WorkflowRun)
    run_id = rm.create(
        WorkflowRun(item_id="it", captured_user="u", status=RunStatus.RUNNING)
    ).resource_id

    await orch._post_run(run_id, "it", "it")

    assert released == []  # RUNNING is neither terminal nor awaiting_human → no release


# ─── playground multi/beta fixture run() (beta/run.py 11) ──────────────────


async def test_playground_multi_beta_run_returns_its_marker():
    """Loading and awaiting the `multi/beta` workflow executes its body
    (beta/run.py line 11)."""
    run = load_run_callable("playground", "multi", "beta")
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    assert await run(wf, {}) == {"status": "done", "workflow": "beta"}


# ─── topic-hub →collections run.py edge branches ───────────────────────────


def _collections_run():
    return load_run_callable("topic-hub", "default", "collections")


def _parse_glossary():
    """Reach the module's private `_parse_glossary` via the loaded run.py module
    globals (it's loaded by file path, not importable as a package)."""
    run = _collections_run()
    return run.__globals__["_parse_glossary"]


async def test_no_collections_file_short_circuits():
    """Absent `collections.json` → `_read_collections` hits its except branch
    (lines 49-50) and returns []; the workflow short-circuits to
    `no_collections`."""
    run = _collections_run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    # No collections.json written at all → FileNotFound caught.
    result = await run(wf, {})
    assert result["status"] == "no_collections"
    assert result["message"]  # #100: a human-readable reason, not a bare token


async def test_non_list_collections_json_yields_no_collections():
    """`collections.json` whose root isn't a list skips the entry loop
    (branch 52->58) and resolves to no collections."""
    run = _collections_run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("collections.json", b'{"not": "a list"}')
    result = await run(wf, {})
    assert result["status"] == "no_collections"
    assert result["message"]


async def test_malformed_collection_entries_are_skipped():
    """Within a list, a non-dict entry (branch 54->53) and a dict entry with no
    usable name/id (branch 56->53) are both skipped — leaving no usable
    collections. Since the file is non-empty, the run reports it as malformed
    (a fixable format error), not as an empty set (#100)."""
    run = _collections_run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    # "just-a-string" → not a dict (54->53); {} → no name/id (56->53).
    await wf.write("collections.json", b'["just-a-string", {}]')
    result = await run(wf, {})
    assert result["status"] == "malformed_collections"
    assert result["message"]


async def test_collections_set_but_no_input_files_returns_empty():
    """Collections exist but no upload matches the glob → the workflow returns
    `{"status": "empty", "files": 0}` (line 91)."""
    run = _collections_run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')
    await wf.write("inputs/input.json", b"{}")  # the only inputs/* file → excluded by default
    result = await run(wf, {})
    assert result["status"] == "empty"
    assert result["files"] == 0
    assert result["message"]


def test_parse_glossary_ignores_a_body_line_before_any_header():
    """`_parse_glossary` only appends body lines once a `## ` header opened a
    term; a leading non-header line is ignored (branch 76->72)."""
    parse = _parse_glossary()
    text = "preamble line before any header\n## M4\nThe fourth metal layer.\n"
    assert parse(text) == [("M4", "The fourth metal layer.")]


def _commit_handle(plan: dict, *, landed: bool = True):
    """A handle whose agent turn writes the given classify `plan` (call 1) then a
    glossary template (call 2); ingest/card/landed are fakes. `landed` controls
    the post-ingest collection_has check."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    ingested: list = []
    cards: list = []
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        if len(calls) == 1:
            await wf.write_json("plan/inputs_a.txt.json", plan)
        else:
            await wf.write("glossary.todo.md", "## M4\n")
        return "done"

    async def ingest(coll, path):
        ingested.append((coll, path))
        return f"doc:{coll}:{path}"

    async def upsert_card(coll, keys, title, body):
        cards.append((coll, list(keys), title, body))
        return f"card:{title}"

    async def has(_coll, _path):
        return landed

    wf.drive_turn = drive_turn
    wf._ingest = ingest
    wf._upsert_card = upsert_card
    wf._collection_has = has
    return wf, ingested, cards


async def _seed(wf: WorkflowHandle) -> None:
    await wf.write("inputs/a.txt", b"the M4 layer delaminated")
    await wf.write("inputs/input.json", b"{}")
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')


async def test_commit_skips_non_string_and_blank_terms_in_term_collection():
    """A plan whose `terms` list carries a non-string and a blank entry: the
    `isinstance(t, str) and t.strip()` guard in the commit term→collection map
    skips them (branch 155->154). The valid term still authors a card."""
    plan = {"collection": "Defects", "digest": "d", "terms": ["M4", 123, "   "]}
    run = _collections_run()
    wf, ingested, cards = _commit_handle(plan)
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await wf.write("glossary.todo.md", "## M4\nThe fourth metal layer.")
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result["status"] == "approved"
    assert result["ingested"] == 1
    assert cards == [("Defects", ["M4"], "M4", "The fourth metal layer.")]


async def test_commit_does_not_count_ingest_that_did_not_land():
    """When the post-ingest `collection_has` check returns False (branch
    162->159), the file is ingested but NOT counted toward `ingested`."""
    plan = {"collection": "Defects", "digest": "d", "terms": ["M4"]}
    run = _collections_run()
    wf, ingested, cards = _commit_handle(plan, landed=False)
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await wf.write("glossary.todo.md", "## M4\nThe fourth metal layer.")
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result["status"] == "approved"
    assert result["ingested"] == 0  # landed=False → not counted (162->159)
    assert ingested == [("Defects", "/inputs/a.txt")]  # but the ingest DID run


def test_topic_hub_collections_run_py_exists_on_disk():
    """Sanity anchor for the fixture path the tests load by file path."""
    assert (
        _profiles_root("topic-hub") / "default" / "workflows" / "collections" / "run.py"
    ).is_file()
