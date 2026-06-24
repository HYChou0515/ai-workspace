"""#65: the RCA `ask_knowledge_base` bridge must forward the composer's
reasoning effort to the KB sub-agent. The plumbing seam is
`answer_question(reasoning_effort=…)` → the sub-agent's AgentToolContext;
without it the sub-agent always ran at its config default and the
composer's effort pick was silently dropped.
"""

from __future__ import annotations

import pytest

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


def test_infer_modules_misconfigured_collection_raises_loudly():
    """#66: a configured collection NAME that matches nothing is a loud
    misconfig (a typo would otherwise silently disable KB for every step)."""
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)  # no collections created
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        infer_modules_collection="ghost-collection",
    )
    client = TestClient(app)
    with pytest.raises(ValueError, match="ghost-collection"):
        client.post(f"/a/rca/items/{iid}/messages", json={"content": "q"})
