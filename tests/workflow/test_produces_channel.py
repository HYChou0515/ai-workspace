"""A node whose output is the FILES it wrote (#428 §1, #107).

An agent node had two ways to produce something and both routed the payload through the
model's reply: `outputs` (the reply is a JSON object) and `out` (the reply is the file's
content). So the size of what a node can produce was capped by the model's output limit,
and handing 1000 records to the next step meant the model retyping all 1000 — which is
both the expensive way and the lossy way to move data it already has.

A node holding `exec` can write those files itself, with a loop, and never put the data in
its reply. It just could not SAY so: `validate_def` demanded `outputs` or `out`, so a node
whose whole job was "write these files" was not expressible.

`produces` is that declaration. It is not a new way to write files — the tools already
wrote them — it is the node telling the engine what it made, so the engine can gate on it
and the next step can reference it.
"""

import json
from typing import Any

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import build_run, parse_def, validate_def
from workspace_app.workflow.engine import StepFailed
from workspace_app.workflow.handle import WorkflowHandle


def _parse(*steps: dict) -> Any:
    return parse_def(json.dumps({"id": "wf", "phases": [{"id": "p"}], "steps": list(steps)}))


_FETCH = {
    "type": "agent",
    "name": "fetch",
    "phase": "p",
    "tools": ["exec"],
    "produces": "data/*.json",
    "prompt": "write one file per image under data/",
}


# ── it can be said at all ────────────────────────────────────────────────────


def test_a_node_whose_output_is_files_validates():
    assert validate_def(_parse(_FETCH)) == []


def test_produces_is_still_exactly_one_output_kind():
    """D5 stands: one node, one kind of output. `produces` is a third kind, not a second
    one a node may hold alongside `outputs`/`out`."""
    errs = validate_def(_parse({**_FETCH, "outputs": {"n": "int"}}))
    assert errs and "ONE output kind" in errs[0]
    errs = validate_def(_parse({**_FETCH, "out": "o.md", "kind": "text"}))
    assert errs and "ONE output kind" in errs[0]


def test_a_downstream_reference_to_produces_validates():
    m = {
        "type": "map",
        "over": "{steps.fetch.produces}",
        "as": "img",
        "phase": "p",
        "do": [{"type": "sandbox", "run": "get {img.url}", "phase": "p"}],
    }
    assert validate_def(_parse(_FETCH, m)) == []


def test_a_typo_on_the_produces_reference_is_caught():
    bad = {"type": "sandbox", "run": "get {steps.fetch.nope}", "phase": "p"}
    errs = validate_def(_parse(_FETCH, bad))
    assert errs and "no output field 'nope'" in errs[0]


# ── it works ─────────────────────────────────────────────────────────────────


def _wf(store: MemoryFileStore, **fakes: Any) -> WorkflowHandle:
    wf = WorkflowHandle(store=store, workspace_id="ws", config={})
    for k, v in fakes.items():
        setattr(wf, k, v)
    return wf


async def test_the_node_hands_its_files_to_a_map_without_the_model_retyping_them():
    """The scenario this exists for: 1000 records fetched and written by a script, then a
    map over them. The model's reply is prose and is never parsed."""
    store = MemoryFileStore()
    n = 1000
    got: list[str] = []

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        # Stands in for the model running a loop through `exec`.
        for i in range(n):
            await wf.write(f"data/img-{i:04d}.json", json.dumps({"url": f"https://x/{i}.png"}))
        return "Done — I wrote 1000 files."  # prose, deliberately not JSON

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        got.append(cmd)
        return 0, ""

    wf = _wf(store, drive_turn=drive_turn, run_sandbox=run_sandbox)
    d = _parse(
        _FETCH,
        {
            "type": "map",
            "over": "{steps.fetch.produces}",
            "as": "img",
            "phase": "p",
            "do": [{"type": "sandbox", "run": "get {img.url}", "phase": "p"}],
        },
    )
    assert validate_def(d) == []
    assert await build_run(d)(wf, None) == {"status": "done"}
    assert len(got) == n
    assert got[0] == "get https://x/0.png"
    assert got[-1] == "get https://x/999.png"


async def test_a_node_that_produced_nothing_fails_instead_of_flowing_on():
    """The hazard the declaration exists to close: the model says it is done, wrote
    nothing, and the run carries on over an empty set."""
    store = MemoryFileStore()

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        return "All done!"  # wrote nothing

    wf = _wf(store, drive_turn=drive_turn)
    d = _parse({**_FETCH, "retries": 0})
    with pytest.raises(StepFailed) as exc:
        await build_run(d)(wf, None)
    assert "data/*.json" in str(exc.value)  # the run driver turns this into an errored run
