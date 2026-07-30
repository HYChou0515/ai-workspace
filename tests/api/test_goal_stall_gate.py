"""#615 P4: the self-destruct gate — stop an unattended goal that is not moving.

Two different failures, one gate. A transient blip (the model 500s once) must
NOT end the night, because nobody is awake to restart it. An agent genuinely
stuck — erroring twice, or re-issuing the identical tool call — must not spend
the remaining budget discovering that again, so it parks and waits for a person.

The gate counts SEPARATELY from the round budget: recovering from a blip costs a
round, but being stuck costs the night rather than thirty rounds of it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone, RunError, ToolEnd, ToolStart
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.config.schema import OffHoursSettings
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.llm import ILlm
from workspace_app.resources import Conversation, make_spec
from workspace_app.resources.conversation_goal import read_goal
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


class _NeverMet(ILlm):
    """Checker double that always says the goal is unmet, so the only thing
    that can stop the chain is the gate under test."""

    def __init__(self) -> None:
        self.calls = 0

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.calls += 1
        yield ("NOT_MET", False)


class _SameCallEveryTurn:
    """An agent going in circles: byte-identical tool call, every turn."""

    async def run(self, prompt: str, ctx) -> AsyncIterator[AgentEvent]:
        yield ToolStart(call_id="t1", name="read_file", args={"path": "report.md"})
        yield ToolEnd(call_id="t1", output="(empty)")
        yield MessageDelta(text="let me check the report")
        yield RunDone()


class _DifferentCallEachTurn:
    """An agent actually working: a new tool call each turn."""

    def __init__(self) -> None:
        self.turn = 0

    async def run(self, prompt: str, ctx) -> AsyncIterator[AgentEvent]:
        self.turn += 1
        yield ToolStart(call_id=f"t{self.turn}", name="read_file", args={"path": f"{self.turn}.md"})
        yield ToolEnd(call_id=f"t{self.turn}", output="...")
        yield MessageDelta(text="progress")
        yield RunDone()


def _window_around_now() -> str:
    now = datetime.now(UTC)
    return f"{(now - timedelta(hours=2)):%H:%M}-{(now + timedelta(hours=2)):%H:%M}"


def _app(runner, *, checker=None, offhours=True, max_rounds=30):
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    settings = (
        OffHoursSettings(
            window=_window_around_now(),
            timezone="UTC",
            max_rounds=max_rounds,
            # Yielding is P3's concern and is specced there. Disabled here so the
            # gate is what these tests observe: with it on, the human message that
            # seeds the thread would (correctly) make the chain stand down.
            yield_after_human_minutes=0,
        )
        if offhours
        else None
    )
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: "alice",
        goal_checker_llm=checker or _NeverMet(),
        goal_offhours=settings,
    )
    return app, spec, iid


def _today_is_a_workday(spec) -> None:
    from workspace_app.resources.work_calendar import WorkCalendar, upsert_work_calendar

    today = datetime.now(UTC).date().isoformat()
    upsert_work_calendar(spec, WorkCalendar(overrides={today: "work"}), user="alice")


def _wait(fn, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def _messages(spec, rid):
    conv = spec.get_resource_manager(Conversation).get(rid).data
    return conv.messages


def _start_night_goal(client, spec, iid, *, condition="the report exists"):
    _today_is_a_workday(spec)
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    rid = chat["chat_id"]
    base = f"/a/rca/items/{iid}/chats/{rid}"
    client.put(f"{base}/goal", json={"condition": condition, "offhours": True})
    client.post(f"{base}/messages", json={"content": "go"})
    return rid, base


def test_a_stuck_agent_parks_instead_of_spending_the_whole_night():
    """Identical tool call two turns running: the agent is circling. Thirty
    rounds of the same read_file teaches nobody anything, so it stops and waits
    for a person rather than burning the budget to reach the same place."""
    app, spec, iid = _app(_SameCallEveryTurn())
    with TestClient(app) as client:
        rid, _base = _start_night_goal(client, spec, iid)

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "stalled")
        goal = read_goal(spec, rid)
        assert goal is not None
        assert goal.offhours_rounds_used < 5  # nowhere near the 30-round budget
        assert any("卡住" in m.content for m in _messages(spec, rid) if m.role == "goal")


def test_an_agent_that_keeps_changing_what_it_does_is_left_alone():
    """The mirror image, and the one that matters most: a working agent must not
    be killed by the gate. Different call each turn ⇒ not stuck."""
    app, spec, iid = _app(_DifferentCallEachTurn(), max_rounds=3)
    with TestClient(app) as client:
        rid, _base = _start_night_goal(client, spec, iid)

        # It runs out of BUDGET, which is the honest ending — never `stalled`.
        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "exhausted")


def test_one_bad_turn_does_not_end_the_night():
    """Nobody is awake to restart it, so a single failure has to be survivable."""
    app, spec, iid = _app(_WorksThenBlips())
    with TestClient(app) as client:
        rid, _base = _start_night_goal(client, spec, iid)

        # It kept going past the error rather than handing back at 02:00.
        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.offhours_rounds_used >= 3)
        goal = read_goal(spec, rid)
        assert goal is not None and goal.state == "active"


def test_two_bad_turns_running_park_the_goal():
    app, spec, iid = _app(_WorksThenBreaks())
    with TestClient(app) as client:
        rid, _base = _start_night_goal(client, spec, iid)

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "stalled")


def test_during_office_hours_one_error_still_hands_straight_back():
    """#613's rule is unchanged where a human is sitting there: an error stops
    the chain immediately. The retry only exists because at 02:00 there is
    nobody to hand back TO."""
    app, spec, iid = _app(
        ScriptedAgentRunner([RunError(message="boom"), RunDone()]), offhours=False
    )
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "anything"})
        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: any(m.role == "error" for m in _messages(spec, rid)))
        time.sleep(0.4)
        goal = read_goal(spec, rid)
        assert goal is not None
        assert goal.state == "active"
        assert goal.rounds_used == 0  # never continued


class _WorksThenBlips:
    """A night that starts fine, hiccups once, and recovers. Turn 1 must succeed
    so the chain reaches its first UNATTENDED round — the retry rule only
    applies once the driver, not a person, is the one asking."""

    def __init__(self) -> None:
        self.turn = 0

    async def run(self, prompt: str, ctx) -> AsyncIterator[AgentEvent]:
        self.turn += 1
        if self.turn == 2:
            yield RunError(message="upstream hiccup")
            return
        yield ToolStart(call_id=f"t{self.turn}", name="read_file", args={"path": f"{self.turn}.md"})
        yield ToolEnd(call_id=f"t{self.turn}", output="...")
        yield MessageDelta(text="working")
        yield RunDone()


class _WorksThenBreaks:
    """Fine once, then fails every turn — the genuinely broken night."""

    def __init__(self) -> None:
        self.turn = 0

    async def run(self, prompt: str, ctx) -> AsyncIterator[AgentEvent]:
        self.turn += 1
        if self.turn == 1:
            yield ToolStart(call_id="t1", name="read_file", args={"path": "1.md"})
            yield ToolEnd(call_id="t1", output="...")
            yield MessageDelta(text="working")
            yield RunDone()
            return
        yield RunError(message="boom")
