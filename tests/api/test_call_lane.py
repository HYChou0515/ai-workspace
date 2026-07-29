"""Which lane a turn runs on — is a person waiting on it?

The external LLM gateway rate-limits the two lanes differently, so the answer has
to be right at the point the turn is built. The hard case is that a human send and
a goal auto-continue round go through the SAME `ChatSendService.send`: the method
cannot tell them apart, so the lane comes from the caller, and the default is the
tighter one.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.conversation_goal import read_goal
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


class _LaneCapture:
    """Records the lane of every turn it is asked to run, in order."""

    def __init__(self) -> None:
        self.lanes: list[str] = []

    async def run(self, prompt: str, ctx: AgentToolContext):
        self.lanes.append(ctx.call_lane)
        yield MessageDelta(text="ok")
        yield RunDone()


class _ScriptLlm(ILlm):
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self.answers.pop(0) if self.answers else "NOT_MET", False)


def _wait(fn, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if v := fn():
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_a_chat_message_a_person_sent_runs_on_the_interactive_lane():
    cap = _LaneCapture()
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=cap,
        get_user_id=lambda: "alice",
    )
    with TestClient(app) as client:
        client.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})
        _wait(lambda: cap.lanes)
    assert cap.lanes == ["interactive"]


async def test_a_kb_chat_message_runs_on_the_interactive_lane():
    """The KB chat is the other surface a person sends from — it builds its own
    AgentToolContext, so the RCA wiring alone would leave it on the tighter lane."""
    from httpx import ASGITransport

    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.resources.kb import EMBED_DIM

    from ._client import AsyncClient

    cap = _LaneCapture()
    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=cap,
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/chats", json={})).json()["resource_id"]
        await c.post(f"/kb/chats/{cid}/messages", json={"content": "go"})
    assert cap.lanes == ["interactive"]


async def test_a_sub_agent_inherits_its_callers_lane():
    """`ask_knowledge_base` spawns a KB sub-agent in its own context. A person is
    still waiting on the answer, so it belongs on the caller's lane — the same way
    a sub-agent inherits its caller's output ceilings."""
    from httpx import ASGITransport

    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.resources.kb import EMBED_DIM

    from ._client import AsyncClient

    class _AsksTheKb(_LaneCapture):
        async def run(self, prompt: str, ctx: AgentToolContext):
            self.lanes.append(ctx.call_lane)
            if ctx.run_subagent is not None and len(self.lanes) == 1:
                await ctx.run_subagent("kb_chat", "what is a defect?")
            yield RunDone()

    cap = _AsksTheKb()
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=cap,
        get_user_id=lambda: "alice",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})
    assert cap.lanes == ["interactive", "interactive"]


def test_a_goal_auto_continue_round_runs_on_the_background_lane():
    """Same `send()`, nobody watching: the system decided to keep going on its own,
    so the round must not spend the quota an interactive turn is waiting on."""
    cap = _LaneCapture()
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=cap,
        get_user_id=lambda: "alice",
        goal_checker_llm=_ScriptLlm(["NOT_MET", "MET"]),
        goal_max_rounds=3,
    )
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "the report exists"})
        client.post(f"{base}/messages", json={"content": "go"})
        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "met")
    # turn 1 the person sent, turn 2 the goal driver sent
    assert cap.lanes == ["interactive", "background"]
