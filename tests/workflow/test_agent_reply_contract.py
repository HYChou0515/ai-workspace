"""What a workflow agent node takes as the model's ANSWER, and what happens when it
misses (#428 §1.2, #107).

A step's reply used to be every assistant message of the turn joined with newlines. The
reducer starts a fresh assistant message after each tool message (`_TurnReducer.
_add_assistant`), so the moment a node's model narrates before calling a tool — which is
what models do, and what a node holding tools needs them to do — the narration was glued
in front of the answer. For a `outputs` node that meant `json.loads` on
`"let me look\n{...}"`, i.e. a node that could essentially never pass.

The answer of a turn is its LAST assistant message, the same as every chat surface. And a
node that misses gets told why and answers again, rather than failing the whole run on one
formatting slip.
"""

import json
from typing import Any

from tests.api._client import TestClient
from workspace_app.api import (
    MessageDelta,
    RunDone,
    ScriptedAgentRunner,
    ToolEnd,
    ToolStart,
    create_app,
)
from workspace_app.api.turns import TurnMessage
from workspace_app.api.workflow_exec import _reply_text
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

# ── the rule itself ──────────────────────────────────────────────────────────


def _a(content: str) -> TurnMessage:
    return TurnMessage(role="assistant", content=content)


def _t(output: str) -> TurnMessage:
    return TurnMessage(role="tool", content=output)


def test_the_answer_is_the_last_assistant_message():
    """Narration, a tool round, then the answer — the answer is what the node gets."""
    produced = [_a("I'll read the file first."), _t("<file contents>"), _a('{"n": 3}')]
    assert _reply_text(produced) == '{"n": 3}'


def test_an_empty_assistant_message_is_not_the_answer():
    """A tool-call turn leaves an assistant message with no text; it must not shadow the
    real answer that came before it."""
    produced = [_a('{"n": 3}'), _t("ok"), _a("   ")]
    assert _reply_text(produced) == '{"n": 3}'


def test_no_assistant_text_at_all_is_an_empty_answer():
    """Nothing to answer with → "" so the node's gate reports it, rather than raising."""
    assert _reply_text([_t("ok")]) == ""


def test_a_single_message_is_unchanged():
    assert _reply_text([_a("the whole document")]) == "the whole document"


# ── the real path: a node that narrates, uses a tool, then answers ───────────

_WF = json.dumps(
    {
        "id": "flow",
        "phases": [{"id": "p"}],
        "steps": [
            {
                "type": "agent",
                "name": "plan",
                "phase": "p",
                "outputs": {"job_count": "int", "job_id": "str"},
                "prompt": "plan the jobs",
            },
            {
                "type": "agent",
                "phase": "p",
                "prompt": "id={steps.plan.job_id} n={steps.plan.job_count}",
                "out": "note.md",
            },
        ],
    }
)

_ANSWER = '{"job_count": 3, "job_id": "abc"}'


class _SequenceRunner:
    """A runner with a different script per turn — so a retry can answer differently
    from the attempt that missed. `ScriptedAgentRunner` replays one script forever,
    which cannot express "got it wrong, then got it right"."""

    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = list(turns)

    async def run(self, prompt: str, ctx: Any):
        events = self.turns.pop(0) if self.turns else [RunDone()]
        for ev in events:
            yield ev


def _app(runner: Any) -> tuple[Any, str]:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=runner,
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="default"))
        .resource_id
    )
    return app, item_id


def _run_to(client: TestClient, item_id: str, want: str) -> dict:
    import time

    base = f"/a/playground/items/{item_id}"
    r = client.put(f"{base}/files/.workflows/flow.json", content=_WF)
    assert r.status_code == 204
    run_id = client.post(f"{base}/run?workflow_id=flow").json()["run_id"]
    for _ in range(400):
        data = client.get(f"{base}/runs/{run_id}").json()
        if data["status"] == want:
            return data
        time.sleep(0.02)
    raise AssertionError(f"run never reached {want!r}: last={data}")


def test_a_node_that_narrates_before_its_tool_call_still_passes():
    """The whole point: the model says something, calls a tool, then answers with the
    JSON. Before, the narration was joined in front of it and the node could not pass."""
    app, item_id = _app(
        ScriptedAgentRunner(
            [
                MessageDelta(text="Let me check the workspace first."),
                ToolStart(call_id="c1", name="list_files", args={}),
                ToolEnd(call_id="c1", output="note.md"),
                MessageDelta(text=_ANSWER),
                RunDone(),
            ]
        )
    )
    with TestClient(app) as client:
        data = _run_to(client, item_id, "done")
    assert data["status"] == "done"


def test_a_node_that_misses_its_shape_is_asked_again():
    """A formatting slip must cost one more turn, not the whole run. The workflow below
    sets no `retries` — the default has to be enough for the model to be told what was
    wrong and answer again, because an author cannot know in advance which node a model
    will fumble."""
    runner = _SequenceRunner(
        [
            [MessageDelta(text="Sure! Here are the jobs."), RunDone()],  # not JSON
            [MessageDelta(text=_ANSWER), RunDone()],  # told why, answers again
            [MessageDelta(text="a note"), RunDone()],  # the prose node after it
        ]
    )
    app, item_id = _app(runner)
    with TestClient(app) as client:
        data = _run_to(client, item_id, "done")
    assert data["status"] == "done"
    assert runner.turns == []  # the retry really happened — all three scripts were used


# The write-back this file used to assert on every turn is now asked for only by the node
# that needs it — see `test_produces_reconcile.py`, which pins both halves: a `produces`
# node reconciles before it globs, and an ordinary turn does not pay for a workspace walk
# it never reads.
