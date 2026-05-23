"""Agent picker — the investigation's attached AgentConfig drives the
live agent's model + prompt (#11).

Note: the production code for these behaviours was written before these
tests (a TDD slip), so this file is characterization — it pins the
behaviour we shipped rather than having driven it red→green.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import QB, SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import AgentConfig
from workspace_app.sandbox.mock import MockSandbox

from .conftest import Harness


def test_default_agent_configs_are_seeded(harness: Harness):
    """The picker is never empty: defaults exist after app construction."""
    rm = harness.spec.get_resource_manager(AgentConfig)
    names = {r.data.name for r in rm.list_resources(QB.all())}  # ty: ignore[unresolved-attribute, invalid-argument-type]
    assert "RCA · Qwen3 (local)" in names
    assert "RCA · Claude Opus" in names


def test_get_agent_config_lists_them(harness: Harness):
    resp = harness.client.get("/agent-config")
    assert resp.status_code == 200
    models = {e["data"]["model"] for e in resp.json()}
    assert "ollama_chat/qwen3:14b" in models


def test_attached_config_drives_the_turn():
    """A message turn runs with the investigation's attached AgentConfig
    (model + prompt), surfaced on the tool context."""
    captured: list[AgentConfig | None] = []

    class _CapturingRunner:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            captured.append(ctx.agent_config)
            yield RunDone()

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_CapturingRunner(),
    )
    client = TestClient(app)

    # pick the Claude config and attach it to a fresh investigation
    cfg = next(
        e for e in client.get("/agent-config").json() if e["data"]["model"] == "claude-opus-4-7"
    )
    cfg_id = cfg["revision_info"]["resource_id"]
    inv_id = client.post(
        "/investigation",
        json={"title": "t", "owner": "u", "attached_agent_config_id": cfg_id},
    ).json()["resource_id"]

    resp = client.post(f"/investigations/{inv_id}/messages", json={"content": "hi"})
    _ = resp.text  # drain the SSE stream so the turn runs
    assert captured and captured[0] is not None
    assert captured[0].model == "claude-opus-4-7"


def test_no_attached_config_leaves_turn_on_default():
    """An investigation without an attached config gets ctx.agent_config
    None — the runner falls back to its own default."""
    captured: list[AgentConfig | None] = []

    class _CapturingRunner:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            captured.append(ctx.agent_config)
            yield RunDone()

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_CapturingRunner(),
    )
    client = TestClient(app)
    inv_id = client.post("/investigation", json={"title": "t", "owner": "u"}).json()["resource_id"]
    resp = client.post(f"/investigations/{inv_id}/messages", json={"content": "hi"})
    _ = resp.text
    assert captured == [None]


def test_seeding_is_idempotent(harness: Harness):
    """Re-seeding an app that already has configs adds nothing."""
    from workspace_app.api.app import _seed_agent_configs

    rm = harness.spec.get_resource_manager(AgentConfig)
    before = rm.count_resources(QB.all())  # ty: ignore[invalid-argument-type]
    _seed_agent_configs(harness.spec)
    assert rm.count_resources(QB.all()) == before  # ty: ignore[invalid-argument-type]


def test_stale_attached_config_id_falls_back_to_default():
    """If the attached config was deleted, the turn quietly falls back to
    the runner default rather than 500-ing."""
    captured: list[AgentConfig | None] = []

    class _CapturingRunner:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            captured.append(ctx.agent_config)
            yield RunDone()

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_CapturingRunner(),
    )
    client = TestClient(app)
    inv_id = client.post(
        "/investigation",
        json={"title": "t", "owner": "u", "attached_agent_config_id": "agent-config:gone"},
    ).json()["resource_id"]
    resp = client.post(f"/investigations/{inv_id}/messages", json={"content": "hi"})
    _ = resp.text
    assert captured == [None]
