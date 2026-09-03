"""#721: running out of STEPS is not the same failure as going wrong.

`runner.max_turns` bounds how many steps the agent may take inside ONE turn. A
big problem legitimately needs more than that, and the turn that hits the ceiling
has usually made real progress — it simply did not finish. Treating it as a
failed turn meant the office-hours rule (#613: hand straight back to the human)
fired, so the goal's auto-continue budget was never spent at all: the thread
showed "已達回合上限(30)" while the goal panel showed 0/3, and both were right.

So a step-capped turn now continues the goal, spending a round like any other.
What it does NOT do is change what the person sees: the banner still appears,
deliberately (see the issue — hiding it turned out to need the shared
`reconcile` contract loosened, for half an effect).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from workspace_app.api import create_app
from workspace_app.api.events import MaxTurnsExceeded, MessageDelta, ToolEnd, ToolStart
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


def _capped_runner() -> ScriptedAgentRunner:
    """A turn that does some work and then runs out of steps."""
    return ScriptedAgentRunner([MessageDelta(text="reading the logs"), MaxTurnsExceeded(turns=30)])


def _today_is_a_workday(spec) -> None:  # noqa: ANN001 — SpecStar, matches the sibling suites
    """Pin today as a working day, so the WINDOW — not whichever weekday the
    suite happens to run on — decides whether it is after hours."""
    from workspace_app.resources.work_calendar import WorkCalendar, upsert_work_calendar

    today = datetime.now(UTC).date().isoformat()
    upsert_work_calendar(spec, WorkCalendar(overrides={today: "work"}), user="alice")


def _app(checker: ILlm, *, runner, max_rounds: int = 3, offhours=None):  # noqa: ANN001
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
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


def _start(client, spec, iid, condition: str = "the report exists"):
    chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
    rid = chat["chat_id"]
    base = f"/a/rca/items/{iid}/chats/{rid}"
    client.put(f"{base}/goal", json={"condition": condition})
    return rid, base


def _window_around_now() -> str:
    now = datetime.now(UTC)
    return f"{(now - timedelta(hours=2)):%H:%M}-{(now + timedelta(hours=2)):%H:%M}"


def test_overnight_follows_the_same_rule_and_spends_the_night_budget():
    """One rule, both times of day — decided so that behaviour does not fork on
    the clock, and because "a big problem takes a while" is MORE true when
    nobody is watching.

    Before this, a capped turn at night was read as no-progress twice running
    and parked as stuck at two rounds. Now it spends the NIGHT counter, and the
    work-hours counter stays untouched so the morning does not read the goal as
    exhausted against the much smaller office cap.
    """
    llm = _ScriptLlm([])  # never satisfied
    # `yield_after_human_minutes=0` because the seeding message is a human's and
    # was sent a second ago: at the default 30 minutes the driver correctly
    # stands down on round 2 rather than talking over someone who just spoke,
    # and the test would be measuring THAT gate instead of this one.
    offhours = OffHoursSettings(
        window=_window_around_now(),
        timezone="UTC",
        max_rounds=2,
        yield_after_human_minutes=0,
    )
    app, spec, iid = _app(llm, runner=_NewCallThenOutOfSteps(), max_rounds=1, offhours=offhours)
    with TestClient(app) as client:
        _today_is_a_workday(spec)
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "the report exists", "offhours": True})

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "exhausted")
        goal = read_goal(spec, rid)
        assert goal is not None
        assert goal.offhours_rounds_used == 2  # the night budget is what it spent
        assert goal.rounds_used == 0  # …and the office counter is untouched


class _BrokenLlm(ILlm):
    """The checker's endpoint is down."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        raise RuntimeError("checker endpoint unreachable")
        yield  # pragma: no cover — unreachable, keeps this a generator


def test_a_checker_that_is_down_hands_back_rather_than_continuing(caplog):
    """A step-capped turn now REACHES the checker, where before it returned
    early — so an outage on that endpoint became newly reachable on this path.

    It must fail toward today's behaviour: no continuation, goal left active for
    a person, and no exception escaping the detached follow-up task (which would
    only ever surface as "Task exception was never retrieved" in the log).
    """
    app, spec, iid = _app(_BrokenLlm(), runner=_capped_runner())
    with TestClient(app) as client:
        rid, base = _start(client, spec, iid)

        client.post(f"{base}/messages", json={"content": "go"})
        time.sleep(1.0)  # long enough for the follow-up to have run and failed

        goal = read_goal(spec, rid)
        assert goal is not None and goal.state == "active"  # waiting for a human
        assert goal.rounds_used == 0
        assert not [m for m in _messages(spec, rid) if m.content.startswith("[goal]")]
        assert "Task exception was never retrieved" not in caplog.text


class _SameCallThenOutOfSteps:
    """Circling AND running out of steps: the case the loosened gate must still
    catch, or #721 would have removed the only brake on an unattended loop."""

    async def run(self, prompt: str, ctx):  # noqa: ANN001, ANN201 — mirrors the protocol
        yield ToolStart(call_id="t1", name="read_file", args={"path": "report.md"})
        yield ToolEnd(call_id="t1", output="(empty)")
        yield MaxTurnsExceeded(turns=30)


def test_circling_still_parks_the_goal_even_when_it_runs_out_of_steps():
    """The brake that remains. Identical tool calls two turns running is the
    signal, and it is unaffected by how the turn ended."""
    llm = _ScriptLlm([])  # never satisfied
    app, spec, iid = _app(llm, runner=_SameCallThenOutOfSteps(), max_rounds=10)
    with TestClient(app) as client:
        rid, base = _start(client, spec, iid)

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "stalled")
        goal = read_goal(spec, rid)
        assert goal is not None
        assert goal.rounds_used < 10  # parked long before the budget ran out
        assert any("卡住" in m.content for m in _messages(spec, rid) if m.role == "goal")


class _NewCallThenOutOfSteps:
    """An agent actually working AND running out of room: a different file every
    round, and the step limit every round."""

    def __init__(self) -> None:
        self.turn = 0

    async def run(self, prompt: str, ctx):  # noqa: ANN001, ANN201 — mirrors the protocol
        self.turn += 1
        yield ToolStart(call_id=f"t{self.turn}", name="read_file", args={"path": f"{self.turn}.md"})
        yield ToolEnd(call_id=f"t{self.turn}", output="…")
        yield MaxTurnsExceeded(turns=30)


def test_capped_turns_in_a_row_are_not_mistaken_for_going_in_circles():
    """The stall gate (#615) parks a goal after two turns with no progress, and
    it used to read "not ok" as its signal — so two step-capped turns looked
    identical to an agent re-issuing the same command, and a long job was parked
    for a human two rounds in.

    A DIFFERENT tool call each round, which is what "making progress" looks like
    to the only judgement that can tell: the fingerprint. Written this way on
    purpose — a turn with no tool calls at all fingerprints as "" and can never
    match, so it would pass this test without the gate being right.
    """
    llm = _ScriptLlm([])  # never satisfied — every verdict is NOT_MET
    app, spec, iid = _app(llm, runner=_NewCallThenOutOfSteps(), max_rounds=3)
    with TestClient(app) as client:
        rid, base = _start(client, spec, iid)

        client.post(f"{base}/messages", json={"content": "go"})

        # Spends its whole budget rather than parking at two.
        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "exhausted")
        goal = read_goal(spec, rid)
        assert goal is not None and goal.rounds_used == 3
        assert goal.state == "exhausted"  # ran out of budget, NOT parked as stuck


def test_a_step_capped_turn_that_finished_the_job_still_closes_the_goal():
    """Running out of steps does not mean the work is unfinished — the agent may
    have done it and then run out on the way to saying so. The checker is what
    decides, and a MET verdict closes the goal rather than buying a round."""
    llm = _ScriptLlm(["MET"])
    app, spec, iid = _app(llm, runner=_capped_runner())
    with TestClient(app) as client:
        rid, base = _start(client, spec, iid, condition="done")

        client.post(f"{base}/messages", json={"content": "go"})

        _wait(lambda: (g := read_goal(spec, rid)) is not None and g.state == "met")
        goal = read_goal(spec, rid)
        assert goal is not None and goal.rounds_used == 0  # closing costs nothing
        assert not [m for m in _messages(spec, rid) if m.content.startswith("[goal]")]


def test_a_step_capped_turn_spends_a_round_and_keeps_going():
    """The whole point: 0/3 becomes 1/3, and a continuation actually runs."""
    llm = _ScriptLlm(["NOT_MET", "MET"])
    app, spec, iid = _app(llm, runner=_capped_runner())
    with TestClient(app) as client:
        rid, base = _start(client, spec, iid)

        client.post(f"{base}/messages", json={"content": "go"})

        # Wait for the CONTINUATION, not for the counter. `rounds_used` is bumped
        # at a different moment from the message being written, so waiting on it
        # left a window in which the count below was still 0 — a race that stayed
        # invisible until something else on the machine shifted the timing.
        def _continuations():
            return [
                m
                for m in _messages(spec, rid)
                if m.role == "user" and m.content.startswith("[goal]")
            ]

        _wait(lambda: len(_continuations()) >= 1)
        assert (g := read_goal(spec, rid)) is not None and g.rounds_used >= 1
        continuations = _continuations()
        assert len(continuations) == 1
        assert "the report exists" in continuations[0].content
