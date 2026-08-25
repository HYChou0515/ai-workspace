"""`produces` on a deterministic node (#428 §1, #107).

Bulk output is exactly the work that should not go through a model: writing a thousand
files is a loop, and a `sandbox` step is the node type with no LLM in it at all. Giving
`produces` only to `agent` left the one reliable producer unable to declare what it made,
so an author had to put a model in front of a script to satisfy the schema.

The same rule as the agent node: one output kind, and the glob must match something.
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


_WRITE = {
    "type": "sandbox",
    "name": "fetch",
    "phase": "p",
    "run": "python fetch.py",
    "produces": "data/*.json",
}


def test_a_sandbox_node_can_declare_the_files_it_wrote():
    assert validate_def(_parse(_WRITE)) == []


def test_a_sandbox_node_still_declares_one_output_kind():
    errs = validate_def(_parse({**_WRITE, "outputs": {"n": "int"}}))
    assert errs and "ONE output kind" in errs[0]


def test_a_downstream_map_can_consume_a_sandbox_node_s_files():
    m = {
        "type": "map",
        "over": "{steps.fetch.produces}",
        "as": "img",
        "phase": "p",
        "do": [{"type": "sandbox", "run": "get {img.url}", "phase": "p"}],
    }
    assert validate_def(_parse(_WRITE, m)) == []


def _wf(store: MemoryFileStore, **fakes: Any) -> WorkflowHandle:
    wf = WorkflowHandle(store=store, workspace_id="ws", config={})
    for k, v in fakes.items():
        setattr(wf, k, v)
    return wf


async def test_a_script_hands_1000_files_to_a_map_with_no_model_involved():
    """The whole bulk path, end to end, with no LLM anywhere in it."""
    store = MemoryFileStore()
    n = 1000
    got: list[str] = []

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        if cmd == "python fetch.py":  # the producer's loop
            for i in range(n):
                await wf.write(f"data/img-{i:04d}.json", json.dumps({"url": f"https://x/{i}.png"}))
            return 0, ""
        got.append(cmd)
        return 0, ""

    wf = _wf(store, run_sandbox=run_sandbox)
    d = _parse(
        _WRITE,
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
    assert got[0] == "get https://x/0.png" and got[-1] == "get https://x/999.png"


async def test_a_script_that_wrote_nothing_fails_its_gate():
    store = MemoryFileStore()

    async def run_sandbox(cmd: str, on_output: Any) -> tuple[int, str]:
        return 0, ""  # exit 0, but produced nothing

    wf = _wf(store, run_sandbox=run_sandbox)
    with pytest.raises(StepFailed) as exc:
        await build_run(_parse(_WRITE))(wf, None)
    assert "data/*.json" in str(exc.value)
