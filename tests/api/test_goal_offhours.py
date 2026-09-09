"""#615 P3: the off-hours sweeper — start tonight's round, once, for the goals
that asked for one.

The sweeper only ever KICKS OFF a stretch: the existing turn-end driver
(`_goal_followup`) carries it from there. So these specs are about who gets
started, when, and exactly once across a fleet — not about how far a goal gets.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from specstar import SpecStar

from workspace_app.api.goal_offhours import OffHoursGoalSweeper, SpecstarStretchClaims
from workspace_app.config.schema import OffHoursSettings
from workspace_app.resources.conversation import Conversation, Message
from workspace_app.resources.conversation_goal import (
    ConversationGoal,
    read_goal,
    register_conversation_goal,
    upsert_goal,
)

TAIPEI = "Asia/Taipei"
# Thursday 2026-07-30, 22:00 Taipei — the office is empty.
NIGHT = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)
# The same Thursday at 14:00 Taipei — people are working.
DAY = datetime(2026, 7, 30, 6, 0, tzinfo=UTC)


def _spec_with_goal(
    *, offhours: bool = True, state: str = "active", offhours_rounds_used: int = 0
) -> tuple[SpecStar, str]:
    spec = SpecStar()
    spec.add_model(Conversation)
    register_conversation_goal(spec)
    rm = spec.get_resource_manager(Conversation)
    rm.create(Conversation(item_id="i1"), resource_id="c1")
    upsert_goal(
        spec,
        ConversationGoal(
            conversation_id="c1",
            condition="ship it",
            set_by="alice",
            offhours=offhours,
            state=state,
            offhours_rounds_used=offhours_rounds_used,
        ),
    )
    return spec, "c1"


def _sweeper(
    spec: SpecStar,
    started: list[str],
    *,
    settings=None,
    fail: bool = False,
    abandoned: list[tuple[str, str]] | None = None,
):
    async def start_round(conversation_id: str) -> None:
        started.append(conversation_id)
        if fail:
            raise RuntimeError("the sandbox would not wake")

    async def night_abandoned(conversation_id: str, reason: str) -> None:
        if abandoned is not None:
            abandoned.append((conversation_id, reason))

    return OffHoursGoalSweeper(
        spec,
        settings=settings or OffHoursSettings(window="19:00-08:00", timezone=TAIPEI),
        claims=SpecstarStretchClaims(spec),
        start_round=start_round,
        night_abandoned=night_abandoned,
    )


def _say(spec: SpecStar, cid: str, *, at: datetime, driven_by: str | None = None) -> None:
    """Append a `role="user"` message — a person's unless `driven_by` says
    otherwise (the driver's own rounds look identical in storage)."""
    rm = spec.get_resource_manager(Conversation)
    conv = rm.get(cid).data
    assert isinstance(conv, Conversation)
    conv.messages.append(
        Message(
            role="user",
            content="hi",
            author="alice",
            created_at=int(at.timestamp() * 1000),
            driven_by=driven_by,
        )
    )
    rm.update(cid, conv)


@pytest.mark.asyncio
async def test_starts_a_round_for_an_opted_in_goal_once_the_office_is_empty():
    spec, cid = _spec_with_goal()
    started: list[str] = []
    assert await _sweeper(spec, started).tick(now=NIGHT) == [cid]
    assert started == [cid]


@pytest.mark.asyncio
async def test_never_starts_anything_during_office_hours():
    spec, _cid = _spec_with_goal()
    started: list[str] = []
    assert await _sweeper(spec, started).tick(now=DAY) == []
    assert started == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "over",
    [
        {"offhours": False},  # never opted in
        {"state": "met"},  # already achieved
        {"state": "exhausted"},  # handed back
        {"offhours_rounds_used": 30},  # budget spent, across all its nights
    ],
    ids=["not-opted-in", "met", "exhausted", "budget-spent"],
)
async def test_leaves_alone_a_goal_that_did_not_ask_or_is_finished(over):
    spec, _cid = _spec_with_goal(**over)
    started: list[str] = []
    assert await _sweeper(spec, started).tick(now=NIGHT) == []


@pytest.mark.asyncio
async def test_stands_down_while_its_owner_is_still_talking():
    # The one thing an unattended agent must never do: interrupt the person it
    # is working for. Better late than arguing with them.
    spec, cid = _spec_with_goal()
    _say(spec, cid, at=NIGHT)
    started: list[str] = []
    assert await _sweeper(spec, started).tick(now=NIGHT) == []


@pytest.mark.asyncio
async def test_standing_down_does_not_cost_the_whole_night():
    # Yielding must not burn the stretch's claim: a chat that goes quiet at
    # 22:00 still gets the overnight work its owner asked for.
    spec, cid = _spec_with_goal()
    _say(spec, cid, at=NIGHT)
    started: list[str] = []
    sweeper = _sweeper(spec, started)

    assert await sweeper.tick(now=NIGHT) == []
    later = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)  # an hour later, still night
    assert await sweeper.tick(now=later) == [cid]


@pytest.mark.asyncio
async def test_the_drivers_own_rounds_do_not_count_as_its_owner_talking():
    # The driver's prompt is persisted as `role="user"` and attributed to the
    # goal's setter, so without `driven_by` the agent would read its own last
    # round as "my owner is active" and stand down forever.
    spec, cid = _spec_with_goal()
    _say(spec, cid, at=NIGHT, driven_by="goal")
    started: list[str] = []
    assert await _sweeper(spec, started).tick(now=NIGHT) == [cid]


@pytest.mark.asyncio
async def test_a_goal_is_started_once_per_stretch_not_once_per_tick():
    spec, cid = _spec_with_goal()
    started: list[str] = []
    sweeper = _sweeper(spec, started)

    assert await sweeper.tick(now=NIGHT) == [cid]
    assert await sweeper.tick(now=NIGHT) == []  # the chain is driving it now
    assert started == [cid]


@pytest.mark.asyncio
async def test_the_next_night_is_a_new_stretch():
    # Cumulative budget, not a nightly reset — but a goal that did not finish
    # last night is picked up again this one, with what it has left.
    spec, cid = _spec_with_goal()
    started: list[str] = []
    sweeper = _sweeper(spec, started)

    assert await sweeper.tick(now=NIGHT) == [cid]
    tomorrow_night = datetime(2026, 7, 31, 14, 0, tzinfo=UTC)
    assert await sweeper.tick(now=tomorrow_night) == [cid]


@pytest.mark.asyncio
async def test_only_one_pod_starts_a_given_goal():
    # Every pod sweeps; the CAS claim elects one. Two sweepers over the SAME
    # backend is what a fleet looks like from the store's point of view.
    spec, cid = _spec_with_goal()
    pod_a: list[str] = []
    pod_b: list[str] = []

    assert await _sweeper(spec, pod_a).tick(now=NIGHT) == [cid]
    assert await _sweeper(spec, pod_b).tick(now=NIGHT) == []
    assert pod_b == []


@pytest.mark.asyncio
async def test_the_window_opening_does_not_wake_everything_at_once():
    spec = SpecStar()
    spec.add_model(Conversation)
    register_conversation_goal(spec)
    rm = spec.get_resource_manager(Conversation)
    for n in range(5):
        rm.create(Conversation(item_id="i1"), resource_id=f"c{n}")
        upsert_goal(
            spec,
            ConversationGoal(
                conversation_id=f"c{n}", condition="ship it", set_by="alice", offhours=True
            ),
        )

    started: list[str] = []
    settings = OffHoursSettings(window="19:00-08:00", timezone=TAIPEI, max_concurrent=2)
    sweeper = _sweeper(spec, started, settings=settings)

    assert len(await sweeper.tick(now=NIGHT)) == 2
    # The rest are not dropped — the next tick picks them up.
    assert len(await sweeper.tick(now=NIGHT)) == 2
    assert len(started) == 4


@pytest.mark.asyncio
async def test_one_chat_failing_to_start_costs_it_a_tick_not_its_night():
    # The sweep is the only thing that ever starts unattended work. One chat
    # blowing up must not end the pass for everyone else, and must not eat the
    # claim it never got to use.
    spec, cid = _spec_with_goal()
    calls: list[str] = []

    async def explode(conversation_id: str) -> None:
        calls.append(conversation_id)
        if len(calls) == 1:
            raise RuntimeError("sandbox is having a moment")

    async def _never_called(conversation_id: str, reason: str) -> None:
        raise AssertionError("one failure is not a night given up on")

    sweeper = OffHoursGoalSweeper(
        spec,
        settings=OffHoursSettings(window="19:00-08:00", timezone=TAIPEI),
        claims=SpecstarStretchClaims(spec),
        start_round=explode,
        night_abandoned=_never_called,
    )

    assert await sweeper.tick(now=NIGHT) == []  # nothing started
    assert await sweeper.tick(now=NIGHT) == [cid]  # same night, retried
    assert calls == [cid, cid]


@pytest.mark.asyncio
async def test_a_deploy_with_no_window_never_sweeps():
    spec, _cid = _spec_with_goal()
    started: list[str] = []
    sweeper = _sweeper(spec, started, settings=OffHoursSettings())
    assert await sweeper.tick(now=NIGHT) == []


# ── the two pure readings the driver makes of a thread ───────────────────────


def test_a_turn_with_no_tool_calls_has_no_signature():
    # Deliberately never "stuck": an agent writing prose may be summarising the
    # work it just finished, and killing that is far worse than letting a stuck
    # one spend one more round.
    from workspace_app.api.goal_offhours import turn_signature

    assert turn_signature(Conversation(item_id="i1")) == ""
    assert (
        turn_signature(
            Conversation(item_id="i1", messages=[Message(role="assistant", content="thinking")])
        )
        == ""
    )


def test_the_same_calls_in_a_different_order_are_the_same_turn():
    # Two turns that issued the same commands did the same thing; the order the
    # model happened to emit them in is not progress.
    from workspace_app.api.goal_offhours import turn_signature

    def _turn(*calls: tuple[str, dict]) -> Conversation:
        msgs: list[Message] = [Message(role="user", content="go")]
        msgs += [
            Message(role="tool", content="ok", tool_name=name, tool_args=args)
            for name, args in calls
        ]
        return Conversation(item_id="i1", messages=msgs)

    a = _turn(("read_file", {"path": "a.md"}), ("exec", {"cmd": "ls"}))
    b = _turn(("exec", {"cmd": "ls"}), ("read_file", {"path": "a.md"}))
    c = _turn(("read_file", {"path": "b.md"}), ("exec", {"cmd": "ls"}))
    assert turn_signature(a) == turn_signature(b)
    assert turn_signature(a) != turn_signature(c)


def test_a_thread_nobody_has_spoken_in_reads_as_quiet():
    from workspace_app.api.goal_offhours import last_human_message_ms

    assert last_human_message_ms(Conversation(item_id="i1")) is None


# ── the retry that a start failure earns, and where it stops ────────────────


@pytest.mark.asyncio
async def test_a_start_that_fails_is_retried_within_the_night():
    """A cold box on the first tick must not cost the whole night — that is what
    giving the claim back is for."""
    spec, cid = _spec_with_goal()
    attempts: list[str] = []
    sweeper = _sweeper(spec, attempts, fail=True)

    assert await sweeper.tick(now=NIGHT) == []  # it raised, so nothing started
    assert await sweeper.tick(now=NIGHT.replace(minute=1)) == []
    assert len(attempts) == 2, "the claim went back, so a later tick tried again"


@pytest.mark.asyncio
async def test_a_night_that_will_not_start_stops_trying():
    """Retrying is only right while the failure might be transient, and the
    number of attempts is the only thing that tells the two apart.

    Unbounded, a sandbox that will not wake turned into a minute-by-minute loop
    all night — and each attempt persists the driver's message and an error into
    its owner's thread before it fails, so the loop is not quiet."""
    spec, cid = _spec_with_goal()
    attempts: list[str] = []
    sweeper = _sweeper(spec, attempts, fail=True)

    for minute in range(30):
        assert await sweeper.tick(now=NIGHT.replace(minute=minute)) == []
    # The exact number, not a ceiling: a bound of three that quietly became nine
    # would still be "less than ten", and this test is the only thing holding it.
    assert len(attempts) == 3, f"expected three attempts, got {len(attempts)}"


@pytest.mark.asyncio
async def test_tomorrow_night_starts_over():
    """The bound is on the NIGHT, not on the goal: infrastructure that was down
    at 3am must not cost a goal its remaining nights. Its state is never
    touched, so nothing has to be un-parked by hand."""
    spec, cid = _spec_with_goal()
    attempts: list[str] = []
    sweeper = _sweeper(spec, attempts, fail=True)

    for minute in range(30):
        await sweeper.tick(now=NIGHT.replace(minute=minute))
    spent_tonight = len(attempts)

    tomorrow = NIGHT.replace(day=NIGHT.day + 1)
    assert await sweeper.tick(now=tomorrow) == []
    assert len(attempts) == spent_tonight + 1, "a new stretch tries again"

    goal = read_goal(spec, cid)
    assert goal is not None
    assert goal.state == "active", "a night that could not start does not park the goal"


@pytest.mark.asyncio
async def test_a_night_that_is_given_up_on_says_so():
    """Giving up quietly is the failure this whole bound was supposed to end.

    A start refused by the turn gate (a full workspace, a spent sandbox quota)
    raises BEFORE the driver's message is persisted, so a night lost that way
    leaves NOTHING in the thread — no message, no error, nothing. Those are also
    exactly the conditions that fail all three attempts, tonight and every
    night, so "the thread already shows it" is not true where it matters most."""
    spec, cid = _spec_with_goal()
    attempts: list[str] = []
    abandoned: list[tuple[str, str]] = []
    sweeper = _sweeper(spec, attempts, fail=True, abandoned=abandoned)

    for minute in range(30):
        await sweeper.tick(now=NIGHT.replace(minute=minute))

    assert len(abandoned) == 1, "told once, at the tick that gave up — not per attempt"
    told_cid, reason = abandoned[0]
    assert told_cid == cid
    assert "the sandbox would not wake" in reason, "the person is told WHY, not just that"


@pytest.mark.asyncio
async def test_a_night_that_starts_fine_says_nothing():
    spec, _cid = _spec_with_goal()
    abandoned: list[tuple[str, str]] = []
    await _sweeper(spec, [], abandoned=abandoned).tick(now=NIGHT)
    assert abandoned == []


@pytest.mark.asyncio
async def test_a_release_that_races_a_failure_does_not_roll_it_back():
    """`release` runs on EVERY pod on every tick — the owner-active path takes
    it without ever holding the claim — and it now carries the failure count
    through a read-modify-write. Without a precondition it writes back the count
    it read before a peer's failure landed, and a bound that keeps being rolled
    back is not a bound."""
    from workspace_app.api.goal_offhours import _GoalStretch

    spec, cid = _spec_with_goal()
    pod_a = SpecstarStretchClaims(spec)
    pod_b = SpecstarStretchClaims(spec)
    assert pod_a.try_claim(cid, "tonight")

    rm = spec.get_resource_manager(_GoalStretch)
    real_get = rm.get
    raced: list[int] = []

    def get_then_race(resource_id: str):
        res = real_get(resource_id)
        if not raced:  # exactly once: pod A's failure lands mid-release
            raced.append(1)
            pod_a.note_failure(cid, "tonight")
        return res

    rm.get = get_then_race  # ty: ignore[invalid-assignment]
    try:
        pod_b.release(cid)
    finally:
        rm.get = real_get  # ty: ignore[invalid-assignment]

    assert raced, "the interleaving under test never happened"
    assert pod_a.note_failure(cid, "tonight") == 2, (
        "pod B's release wrote back a count from before pod A's failure"
    )


@pytest.mark.asyncio
async def test_standing_down_for_a_person_does_not_roll_back_the_failure_count():
    """`release` carries the failure count through it, so it needs the same
    precondition `note_failure` uses. Without one, a peer pod standing down for a
    human writes back the count it read before ours landed — and a bound that
    keeps being rolled back is not a bound."""
    spec, cid = _spec_with_goal()
    claims = SpecstarStretchClaims(spec)
    assert claims.try_claim(cid, "tonight")
    assert claims.note_failure(cid, "tonight") == 1
    claims.release(cid)
    assert claims.try_claim(cid, "tonight")
    assert claims.note_failure(cid, "tonight") == 2, (
        "the release must carry the count through, not reset it"
    )


@pytest.mark.asyncio
async def test_a_person_speaking_does_not_undo_a_night_already_given_up_on():
    """Standing down for a person gives the claim back — and the claim is the
    only thing holding the give-up, so the night restarted itself.

    The loop closes on itself, which is what makes it bad: the bell this ending
    rings says 今晚沒能開始, and a person who answers it in the chat is exactly
    the message that stands the sweeper down and hands the night back. Measured
    at six tellings and eight attempts for one night of glancing at the screen."""
    spec, cid = _spec_with_goal()
    attempts: list[str] = []
    abandoned: list[tuple[str, str]] = []
    sweeper = _sweeper(spec, attempts, fail=True, abandoned=abandoned)

    for minute in range(5):
        await sweeper.tick(now=NIGHT.replace(minute=minute))
    assert len(abandoned) == 1 and len(attempts) == 3

    # Someone reads the bell and replies. The sweeper stands down for them…
    _say(spec, cid, at=NIGHT.replace(minute=6))
    for minute in range(6, 40):
        await sweeper.tick(now=NIGHT.replace(minute=minute))

    # …and, once they are quiet again, must NOT pick tonight back up.
    assert len(attempts) == 3, f"the night restarted itself: {len(attempts)} attempts"
    assert len(abandoned) == 1, f"told {len(abandoned)} times for one night"


@pytest.mark.asyncio
async def test_a_failure_to_TELL_one_chat_does_not_end_the_sweep_either():
    """The handler this runs in exists so one chat's failure cannot end the pass
    for the fleet. Telling is done inside it, so it needs the same treatment as
    everything else there — an orphaned goal whose chat is gone raises on the
    read, and that is a real state (`item_routes` names it)."""
    spec, first = _spec_with_goal()
    rm = spec.get_resource_manager(Conversation)
    rm.create(Conversation(item_id="i2"), resource_id="c2")
    upsert_goal(
        spec,
        ConversationGoal(
            conversation_id="c2", condition="ship it too", set_by="bob", offhours=True
        ),
        user="bob",
    )

    attempts: list[str] = []

    async def start_round(conversation_id: str) -> None:
        attempts.append(conversation_id)
        raise RuntimeError("the sandbox would not wake")

    async def telling_explodes(conversation_id: str, reason: str) -> None:
        raise RuntimeError("that chat was deleted while we were failing")

    sweeper = OffHoursGoalSweeper(
        spec,
        settings=OffHoursSettings(window="19:00-08:00", timezone=TAIPEI),
        claims=SpecstarStretchClaims(spec),
        start_round=start_round,
        night_abandoned=telling_explodes,
    )

    for minute in range(5):
        await sweeper.tick(now=NIGHT.replace(minute=minute))

    assert first in attempts and "c2" in attempts, (
        "the chat whose telling blew up took the whole sweep down with it"
    )
