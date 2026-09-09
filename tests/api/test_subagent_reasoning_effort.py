"""#65: the RCA `ask_knowledge_base` bridge must forward the composer's
reasoning effort to the KB sub-agent. The plumbing seam is
`answer_question(reasoning_effort=…)` → the sub-agent's AgentToolContext;
without it the sub-agent always ran at its config default and the
composer's effort pick was silently dropped.
"""

from __future__ import annotations

import time

from specstar import QB

from workspace_app.api import ScriptedAgentRunner, create_app  # noqa: F401 — re-export sanity
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.api.kb_chat_routes import answer_question
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import AgentConfig, Collection, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


class _CaptureRunner:
    """Records the reasoning effort it was handed on the run context."""

    def __init__(self) -> None:
        self.seen: str | None = "UNSET"

    async def run(self, prompt, ctx):  # noqa: ANN001 — test double
        self.seen = ctx.reasoning_effort
        yield MessageDelta(text="ok")


async def test_answer_question_sets_reasoning_effort_on_subagent_ctx():
    runner = _CaptureRunner()
    await answer_question(
        runner,  # type: ignore[arg-type] — test double satisfies the run() shape
        retriever=None,  # ty: ignore[invalid-argument-type] — _CaptureRunner never retrieves
        collection_ids=[],
        question="q",
        agent_config=AgentConfig(name="KB"),
        reasoning_effort="high",
    )
    assert runner.seen == "high"


async def test_answer_question_defaults_reasoning_effort_to_none():
    runner = _CaptureRunner()
    await answer_question(
        runner,  # type: ignore[arg-type]
        retriever=None,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        collection_ids=[],
        question="q",
        agent_config=AgentConfig(name="KB"),
    )
    assert runner.seen is None


def test_ask_knowledge_base_forwards_composer_reasoning_effort_end_to_end():
    """The RCA message endpoint's reasoning-effort pick must reach the KB
    sub-agent that `ask_knowledge_base` spawns — the whole point of #65."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    seen: dict[str, str | None] = {}

    class _Runner:
        async def run(self, prompt, ctx):  # noqa: ANN001 — test double
            # The KB sub-agent turn has no sandbox (it's the KB flavour of
            # AgentToolContext); the outer RCA turn does.
            if ctx.sandbox is None:
                seen["effort"] = ctx.reasoning_effort
                yield MessageDelta(text="kb answer")
                return
            await ctx.run_subagent("kb_chat", "what do the docs say?")  # type: ignore[misc]
            yield MessageDelta(text="done")

    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_Runner()
    )
    client = TestClient(app)
    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "q", "reasoning_effort": "high"},
    )
    assert seen.get("effort") == "high"


def test_infer_modules_scopes_kb_search_to_the_configured_collection():
    """#66: infer_modules' per-step classifier searches ONLY the configured
    collection (resolved once per turn), not every collection — so ~1500
    classifications don't re-list + over-search the whole KB."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    crm = spec.get_resource_manager(Collection)
    wanted = crm.create(Collection(name="fab-process-docs")).resource_id
    crm.create(Collection(name="unrelated"))  # must NOT be searched
    seen: dict[str, list[str]] = {}

    class _Runner:
        async def run(self, prompt, ctx):  # noqa: ANN001 — test double
            if ctx.sandbox is None:  # the KB sub-agent turn
                seen["colls"] = list(ctx.collection_ids)
                yield MessageDelta(text='{"module": "STI", "reason": "x"}')
                return
            await ctx.run_subagent("infer_modules", '{"step_name": "S1"}')  # type: ignore[misc]
            yield MessageDelta(text="done")

    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        infer_modules_collection="fab-process-docs",
    )
    client = TestClient(app)
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
    assert seen.get("colls") == [wanted]


def test_infer_modules_misconfigured_collection_is_still_loud():
    """#66: a configured collection NAME that matches nothing is a loud
    misconfig (a typo would otherwise silently disable KB for every step).

    It used to be loud by escaping the request. It cannot be any more: the check
    runs AFTER the user's message is persisted, and this endpoint now answers 202
    the moment the write lands — a 500 there would tell the client the message
    did not happen when it did. So the loudness moved rather than went away, and
    this test moved with it: the misconfig is written into the thread as an error
    the person can see, and `logger.exception` puts the traceback in front of
    whoever configured it."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)  # no collections created
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        infer_modules_collection="ghost-collection",
    )
    with TestClient(app) as client:
        r = client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
        assert r.status_code == 202, "the message was written, so it was accepted"

        errors: list[str] = []
        for _ in range(100):
            conv = _thread(spec, iid)
            errors = [m.content for m in conv.messages if m.role == "error"]
            if errors:
                break
            time.sleep(0.05)

    assert errors, "the misconfig left nothing behind for anyone to see"
    assert any("ghost-collection" in e for e in errors), errors


def _thread(spec, iid: str):  # noqa: ANN001, ANN202
    from workspace_app.resources import Conversation

    rm = spec.get_resource_manager(Conversation)
    for r in rm.list_resources(QB.all()):
        data = r.data
        if isinstance(data, Conversation) and data.item_id == iid:
            return data
    raise AssertionError(f"no conversation for {iid}")
