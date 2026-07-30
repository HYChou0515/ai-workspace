"""#613 P3: the goal auto-continue driver — after each turn a cheap LLM judges
the chat's goal condition; unmet + budget left ⇒ the chat continues itself with
a visible `[goal]` user message; met ⇒ the goal closes with a marker; the round
budget is hard. Driven end-to-end through create_app + ScriptedAgentRunner with
a scripted checker LLM (`with TestClient` so the app loop hosts the detached
follow-up tasks).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from workspace_app.api import create_app
from workspace_app.api.events import MessageDelta, RunDone, RunError
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.config.schema import OffHoursSettings
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.llm import ILlm
from workspace_app.resources import Conversation, make_spec
from workspace_app.resources.conversation_goal import read_goal
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


class _ScriptLlm(ILlm):
    """Checker double: yields the scripted verdict per call, records prompts."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield (self.answers.pop(0) if self.answers else "NOT_MET", False)


def _app(checker: ILlm | None, *, max_rounds: int = 3, runner=None, offhours=None):
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner or ScriptedAgentRunner([MessageDelta(text="working on it"), RunDone()]),
        get_user_id=lambda: "alice",
        goal_checker_llm=checker,
        goal_max_rounds=max_rounds,
        goal_offhours=offhours,
    )
    return app, spec, iid


def _wait(fn, timeout: float = 15.0):
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


def test_unmet_then_met_continues_once_and_closes_the_goal():
    """Turn 1: checker says NOT_MET → a visible `[goal]` user message drives a
    second turn (rounds_used=1). Turn 2: MET → state flips to met and a goal
    marker lands in the thread; no third turn starts."""
    llm = _ScriptLlm(["NOT_MET", "MET"])
    app, spec, iid = _app(llm)
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "the report exists"})

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "met")
        goal = read_goal(spec, rid)
        assert goal is not None and goal.rounds_used == 1
        msgs = _messages(spec, rid)
        continuations = [m for m in msgs if m.role == "user" and m.content.startswith("[goal]")]
        assert len(continuations) == 1
        assert "the report exists" in continuations[0].content
        assert any(m.role == "goal" for m in msgs)  # the completion marker
        assert len(llm.prompts) == 2


def test_met_on_the_first_turn_closes_without_continuing():
    llm = _ScriptLlm(["MET"])
    app, spec, iid = _app(llm)
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "done"})

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "met")
        goal = read_goal(spec, rid)
        assert goal is not None and goal.rounds_used == 0
        assert not [m for m in _messages(spec, rid) if m.content.startswith("[goal]")]


def test_budget_exhausts_and_parks_for_a_human():
    """Checker never satisfied + max_rounds=1: exactly one continuation runs,
    then the goal parks as `exhausted` with a marker — the hard cap is what
    keeps an unreliable small-model verdict from looping forever."""
    llm = _ScriptLlm([])  # empty script ⇒ always NOT_MET
    app, spec, iid = _app(llm, max_rounds=1)
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "never true"})

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "exhausted")
        goal = read_goal(spec, rid)
        assert goal is not None and goal.rounds_used == 1
        msgs = _messages(spec, rid)
        assert len([m for m in msgs if m.role == "user" and m.content.startswith("[goal]")]) == 1
        assert any(m.role == "goal" for m in msgs)  # the exhausted marker


def test_without_a_goal_the_checker_is_never_called():
    llm = _ScriptLlm([])
    app, spec, iid = _app(llm)
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        client.post(f"/a/rca/items/{iid}/chats/{rid}/messages", json={"content": "go"})
        _wait(lambda: any(m.role == "assistant" for m in _messages(spec, rid)))
        time.sleep(0.3)  # give a wrong follow-up task the chance to fire
        assert llm.prompts == []


def test_a_failed_turn_never_auto_continues():
    """The turn ended in an error (incl. user Stop → cancelled): auto-continuing
    would run away from a human who just intervened — the guard skips the
    checker entirely."""
    llm = _ScriptLlm([])
    app, spec, iid = _app(llm, runner=ScriptedAgentRunner([RunError(message="boom"), RunDone()]))
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "anything"})

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: any(m.role == "error" for m in _messages(spec, rid)))
        time.sleep(0.3)
        assert llm.prompts == []
        goal = read_goal(spec, rid)
        assert goal is not None and goal.state == "active" and goal.rounds_used == 0


# ── #615: which budget a continuation spends, and where a night ends ─────────
#
# The clock is not mocked; the WINDOW is moved instead, which is the same knob
# an operator turns and keeps these tests honest about the real code path.


def _today_is_a_workday(spec) -> None:
    """Pin today as a working day, so the window — not the weekday the suite
    happens to run on — decides whether it is after hours."""
    from workspace_app.resources.work_calendar import WorkCalendar, upsert_work_calendar

    today = datetime.now(UTC).date().isoformat()
    upsert_work_calendar(spec, WorkCalendar(overrides={today: "work"}), user="alice")


def _window_around_now() -> str:
    now = datetime.now(UTC)
    return f"{(now - timedelta(hours=2)):%H:%M}-{(now + timedelta(hours=2)):%H:%M}"


def _window_excluding_now() -> str:
    now = datetime.now(UTC)
    return f"{(now + timedelta(hours=2)):%H:%M}-{(now + timedelta(hours=4)):%H:%M}"


def test_a_night_round_spends_the_night_budget_not_the_daytime_one():
    """The whole reason there are two counters: a night's work must not make the
    goal read as exhausted against the (much smaller) work-hours cap."""
    llm = _ScriptLlm(["NOT_MET", "MET"])
    offhours = OffHoursSettings(window=_window_around_now(), timezone="UTC", max_rounds=30)
    app, spec, iid = _app(llm, max_rounds=1, offhours=offhours)
    with TestClient(app) as client:
        _today_is_a_workday(spec)
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "the report exists", "offhours": True})

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "met")
        goal = read_goal(spec, rid)
        assert goal is not None
        # Spent a NIGHT round; the work-hours counter (capped at 1 here) is
        # untouched, so tomorrow morning the goal is not falsely exhausted.
        assert goal.offhours_rounds_used == 1
        assert goal.rounds_used == 0


def test_the_night_ends_at_the_morning_edge_with_the_goal_still_alive():
    """Back at office hours the chain stops — but the goal is NOT exhausted. It
    is waiting for tonight, and the sweeper will pick it up again."""
    llm = _ScriptLlm(["NOT_MET", "NOT_MET"])
    offhours = OffHoursSettings(window=_window_excluding_now(), timezone="UTC", max_rounds=30)
    app, spec, iid = _app(llm, offhours=offhours)
    with TestClient(app) as client:
        _today_is_a_workday(spec)
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "the report exists", "offhours": True})

        client.post(f"{base}/messages", json={"content": "go"})

        # Turn 1 was asked for by a person, so it continues on the work-hours
        # budget. Turn 2 is the DRIVER's, and by then the window has shut — that
        # is the round the chain has to stop after.
        marker = _wait(lambda: [m for m in _messages(spec, rid) if m.role == "goal"])[0]
        # The hand-over says what happens next, not just that it stopped.
        assert "今天晚上會自己接著做" in marker.content

        goal = read_goal(spec, rid)
        assert goal is not None
        assert goal.state == "active"  # waiting for tonight, not handed back
        assert goal.offhours_rounds_used == 0  # nothing was spent off the clock
