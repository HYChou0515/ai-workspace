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


def _sweeper(spec: SpecStar, started: list[str], *, settings=None):
    async def start_round(conversation_id: str) -> None:
        started.append(conversation_id)

    return OffHoursGoalSweeper(
        spec,
        settings=settings or OffHoursSettings(window="19:00-08:00", timezone=TAIPEI),
        claims=SpecstarStretchClaims(spec),
        start_round=start_round,
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

    sweeper = OffHoursGoalSweeper(
        spec,
        settings=OffHoursSettings(window="19:00-08:00", timezone=TAIPEI),
        claims=SpecstarStretchClaims(spec),
        start_round=explode,
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
