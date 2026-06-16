"""P4 — @mention: a pure 'come look' summon (no agent run); humans via the
endpoint, the agent via its mention_user tool."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
from specstar import QB, SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox


def _conversation_messages(spec: SpecStar, investigation_id: str):
    rm = spec.get_resource_manager(Conversation)
    for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        data = r.data
        assert isinstance(data, Conversation)
        if data.item_id == investigation_id:
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
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner or _Idle(),  # ty: ignore[invalid-argument-type]
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec


def _make_rca_item(c: TestClient) -> str:
    return c.post("/a/rca/items", json={"title": "Reflow drift"}).json()["resource_id"]


def test_mention_records_an_entry_and_notifies_others_not_self():
    """A new per-App WorkItem isn't in the legacy Investigation table, so the
    mention must resolve its title via the generic work-item lookup."""
    holder = {"id": "alice"}
    c, spec = _client(holder)
    item = _make_rca_item(c)

    r = c.post(
        f"/a/rca/items/{item}/mentions",
        json={"user_ids": ["bob", "carol", "alice"], "note": "please look"},
    )
    assert r.status_code == 204

    # a mention entry landed in the conversation (human-to-human, not an agent turn)
    msgs = _conversation_messages(spec, item)
    mention = next(m for m in msgs if m.role == "mention")
    assert mention.author == "alice"
    assert mention.mentions == ["bob", "carol", "alice"]
    assert mention.content == "please look"

    # bob + carol notified; alice (the actor) is not summoned to her own case
    for uid in ("bob", "carol"):
        holder["id"] = uid
        ns = c.get("/notifications").json()
        assert any(n["kind"] == "mention" and n["link"] == f"/a/rca/items/{item}" for n in ns)
    holder["id"] = "alice"
    assert c.get("/notifications").json() == []


def test_mention_missing_item_404s():
    holder = {"id": "alice"}
    c, _ = _client(holder)
    assert c.post("/a/rca/items/nope/mentions", json={"user_ids": ["bob"]}).status_code == 404


def test_agent_can_mention_a_user():
    """The agent's mention_user hook resolves the item's title through the same
    generic lookup, so summoning on a new App item works."""
    holder = {"id": "alice"}
    c, spec = _client(holder, runner=_MentioningAgent())
    item = _make_rca_item(c)

    # a normal agent turn whose agent calls mention_user → bob summoned
    r = c.post(f"/a/rca/items/{item}/messages", json={"content": "who knows reflow?"})
    assert r.status_code == 202  # #43: queued; POST awaits the turn then 202

    msgs = _conversation_messages(spec, item)
    agent_mention = next(m for m in msgs if m.role == "mention")
    assert agent_mention.author == "RCA Agent"
    assert agent_mention.mentions == ["bob"]

    holder["id"] = "bob"
    ns = c.get("/notifications").json()
    assert any(n["kind"] == "mention" and n["actor"] is None for n in ns)  # agent-sent
