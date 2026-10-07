"""When a pod writes its skill hub counts (plan-skill-hub-history §4.8, U4):
on a timer, and when it stops — a SIGTERM'd pod does not take two hours of
counts with it."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.skill_hub import SkillHubReview, SkillHubStore
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _entry(hub: SkillHubStore) -> str:
    """A real entry: counts for an id that names none are dropped at the flush."""
    return asyncio.run(
        hub.publish(
            owner="alice",
            name="triage",
            description="d",
            source_item="i",
            source_app="rca",
            source_profile="default",
            payload={"SKILL.md": b"---\nname: triage\ndescription: d\n---\nx"},
            referenced_tools=[],
            review=SkillHubReview(verdict="ok"),
        )
    )


def _app(**kw):  # noqa: ANN003, ANN202
    return create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        **kw,
    )


def test_stopping_the_pod_writes_what_it_counted() -> None:
    app = _app()
    hub: SkillHubStore = app.state.skill_hub
    e1 = _entry(hub)
    with TestClient(app):
        hub.usage.install(e1, user="bob", item="i1")
        assert hub.usage.totals([e1]) == {e1: (0, 0)}
    assert hub.usage.totals([e1]) == {e1: (1, 0)}


def test_a_running_pod_writes_on_its_interval() -> None:
    app = _app(skill_hub_flush_interval=timedelta(milliseconds=20))
    hub: SkillHubStore = app.state.skill_hub
    e1 = _entry(hub)
    with TestClient(app) as client:
        hub.usage.use(e1, user="bob", item="i1")

        async def wait() -> None:
            for _ in range(200):
                if hub.usage.totals([e1]) == {e1: (0, 1)}:
                    return
                await asyncio.sleep(0.01)

        client.portal.call(wait)  # ty: ignore[unresolved-attribute]
        assert hub.usage.totals([e1]) == {e1: (0, 1)}, "written while still running"


def test_a_flush_that_outlives_the_shutdown_budget_is_left_behind_and_said(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 1 (regression #7): the shutdown flush is inside the shutdown
    budget like every drain before it — a slow store must not hold the pod
    past its grace period."""
    import threading
    from datetime import timedelta

    app = _app(shutdown_budget=timedelta(0))
    hub: SkillHubStore = app.state.skill_hub
    e1 = _entry(hub)
    stuck = threading.Event()
    monkeypatch.setattr(hub.usage, "_add", lambda *_a: stuck.wait(3))
    try:
        with caplog.at_level("WARNING"), TestClient(app):
            hub.usage.use(e1, user="bob", item="i1")
    finally:
        stuck.set()

    assert "skill hub usage flush ran out of shutdown budget" in caplog.text
