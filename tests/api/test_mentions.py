"""P4 — @mention: a pure 'come look' summon (no agent run); humans via the
endpoint, the agent via its mention_user tool."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import QB, SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation
from workspace_app.sandbox.mock import MockSandbox


def _conversation_messages(spec: SpecStar, investigation_id: str):
    rm = spec.get_resource_manager(Conversation)
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        data = r.data
        assert isinstance(data, Conversation)
        if data.investigation_id == investigation_id:
            return data.messages
    return []


class _Idle:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


class _MentioningAgent:
    """Stands in for an agent that calls its mention_user tool mid-turn."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        assert ctx.mention is not None and ctx.investigation_id is not None
        ctx.mention(ctx.investigation_id, ["bob"], "the agent thinks you should see this")
        yield RunDone()


def _client(holder: dict[str, str], runner: object = None) -> tuple[TestClient, SpecStar]:
    spec = SpecStar()
    spec.configure(default_user=lambda: holder["id"], default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner or _Idle(),  # ty: ignore[invalid-argument-type]
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _make_inv(c: TestClient, owner: str) -> str:
    return c.post("/investigation", json={"title": "Reflow drift", "owner": owner}).json()[
        "resource_id"
    ]


def test_mention_records_an_entry_and_notifies_others_not_self():
    holder = {"id": "alice"}
    c, spec = _client(holder)
    inv = _make_inv(c, "alice")

    r = c.post(
        f"/investigations/{inv}/mentions",
        json={"user_ids": ["bob", "carol", "alice"], "note": "please look"},
    )
    assert r.status_code == 204

    # a mention entry landed in the conversation (human-to-human, not an agent turn)
    msgs = _conversation_messages(spec, inv)
    mention = next(m for m in msgs if m.role == "mention")
    assert mention.author == "alice"
    assert mention.mentions == ["bob", "carol", "alice"]
    assert mention.content == "please look"

    # bob + carol notified; alice (the actor) is not summoned to her own case
    for uid in ("bob", "carol"):
        holder["id"] = uid
        ns = c.get("/notifications").json()
        assert any(n["kind"] == "mention" and n["link"] == f"/investigations/{inv}" for n in ns)
    holder["id"] = "alice"
    assert c.get("/notifications").json() == []


def test_mention_missing_investigation_404s():
    holder = {"id": "alice"}
    c, _ = _client(holder)
    assert c.post("/investigations/nope/mentions", json={"user_ids": ["bob"]}).status_code == 404


def test_agent_can_mention_a_user():
    holder = {"id": "alice"}
    c, spec = _client(holder, runner=_MentioningAgent())
    inv = _make_inv(c, "alice")

    # a normal agent turn whose agent calls mention_user → bob summoned
    r = c.post(f"/investigations/{inv}/messages", json={"content": "who knows reflow?"})
    assert r.status_code == 200
    _ = r.text  # drain the stream so the turn runs

    msgs = _conversation_messages(spec, inv)
    agent_mention = next(m for m in msgs if m.role == "mention")
    assert agent_mention.author == "RCA Agent"
    assert agent_mention.mentions == ["bob"]

    holder["id"] = "bob"
    ns = c.get("/notifications").json()
    assert any(n["kind"] == "mention" and n["actor"] is None for n in ns)  # agent-sent
