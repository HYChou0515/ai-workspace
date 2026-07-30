"""#615 P3: the off-hours sweeper — start tonight's round for the goals that asked.

It runs as an API-pod lifespan task, alongside the eight sweepers already there,
NOT as a k8s CronJob and NOT on a worker. Two reasons, and the second is the
real one:

* A turn needs the `ChatTurnEngine`, the sandbox and `ChatSendService`, all of
  which live in the API process. The existing CronJobs are `curl`s that drop a
  job on a queue for a worker to drain — there is no worker here to drop it on.
* A CronJob fires at an INSTANT, and this has to keep asking. "Stand down while
  someone is still working, try again later" is a poll, not a schedule.

The sweeper only ever KICKS OFF a stretch. The turn-end driver
(`ChatSendService._goal_followup`) already continues a goal after every turn, so
once tonight's first round is enqueued the existing chain carries it — through
the budget, the yield check and the morning boundary — with no second driver.

Across a fleet every pod sweeps, so exactly one must win: `SpecstarStretchClaims`
CAS-claims `(conversation_id, stretch)` the way `workflow/triggers.py` claims
`(trigger_id, fire_window)`. The claim is per STRETCH, not per tick, so a goal is
started once per night rather than once a minute — and it is RELEASED when the
sweeper stands down for a human, so a chat that goes quiet at 23:00 can still be
picked up at midnight.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from msgspec import Struct
from specstar import QB, SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from ..config.schema import OffHoursSettings
from ..resources.conversation import Conversation
from ..resources.conversation_goal import ConversationGoal
from ..workcalendar import OffHoursCalendar

logger = logging.getLogger(__name__)

# Real contention is a handful of pods racing one conversation for a few
# microseconds at the top of a stretch (mirrors the trigger-window CAS).
_MAX_CAS_RETRIES = 100


class _GoalStretch(Struct):
    """Which off-hours stretch a conversation's goal has already been started
    for. ``resource_id == conversation_id`` so claiming is a SINGLE-row CAS."""

    conversation_id: str
    stretch: str = ""


def register_stretch_claims(spec: SpecStar) -> None:
    """Idempotently register the claim model. Safe to call on every pod."""
    with contextlib.suppress(ValueError):
        spec.add_model(_GoalStretch)


class SpecstarStretchClaims:
    """Cluster-wide "who starts this goal tonight?" election, one row per chat."""

    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec
        register_stretch_claims(spec)

    def try_claim(self, conversation_id: str, stretch: str) -> bool:
        """True for the single caller that claims ``stretch`` for this chat."""
        rm = self._spec.get_resource_manager(_GoalStretch)
        row = _GoalStretch(conversation_id=conversation_id, stretch=stretch)
        try:
            rm.create(row, resource_id=conversation_id, if_not_exists=True)  # ty: ignore[unknown-argument]
            return True
        except DuplicateResourceError:
            pass  # a row exists — CAS-advance it below
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = rm.get(conversation_id)
            except (ResourceIDNotFoundError, ResourceIsDeletedError):  # pragma: no cover
                return False  # vanished under us — let the next tick retry
            data = res.data
            assert isinstance(data, _GoalStretch)
            if data.stretch == stretch:
                return False  # a peer (or an earlier tick tonight) already has it
            try:
                rm.modify(
                    conversation_id,
                    row,
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                return True
            except PreconditionFailedError:  # pragma: no cover - cross-pod CAS race
                continue  # a peer advanced between our read and write — re-read
        raise RuntimeError(  # pragma: no cover - only under pathological churn
            f"goal stretch claim CAS exhausted retries for {conversation_id!r}"
        )

    def release(self, conversation_id: str) -> None:
        """Give tonight's claim back, so a later tick can take it.

        Standing down for a human must not cost the whole night: without this, a
        chat whose owner said something at 19:05 would stay unclaimable until
        the next evening, and the work they asked for overnight would simply not
        happen."""
        rm = self._spec.get_resource_manager(_GoalStretch)
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            rm.modify(
                conversation_id,
                _GoalStretch(conversation_id=conversation_id, stretch=""),
                status=RevisionStatus.draft,
            )


def build_offhours_calendar(spec: SpecStar, settings: OffHoursSettings) -> OffHoursCalendar:
    """The deployment's off-hours rule: the configured window/zone plus the
    CURRENTLY stored work calendar. Built per use rather than cached — an
    operator recording tomorrow's make-up workday must take effect tonight, not
    after the next restart."""
    from ..resources.work_calendar import read_work_calendar

    stored = read_work_calendar(spec)
    return OffHoursCalendar(
        window=settings.window,
        timezone=settings.timezone,
        workdays=tuple(stored.workdays),
        overrides=stored.overrides,
    )


def owner_is_active(conv: Conversation, now: datetime, settings: OffHoursSettings) -> bool:
    """Has a human spoken in this chat inside the yield window?"""
    spoke_at = last_human_message_ms(conv)
    if spoke_at is None:
        return False
    idle_ms = int(now.timestamp() * 1000) - spoke_at
    return idle_ms < settings.yield_after_human_minutes * 60_000


def turn_signature(conv: Conversation) -> str:
    """A fingerprint of the tool calls the last turn made, or `""` when it made
    none (#615).

    Two identical fingerprints in a row is the cheap, deterministic shape of an
    agent going in circles — re-issuing the same command and re-reading the same
    answer. No extra model call, and no judgement a small model could get wrong.

    A turn with NO tool calls fingerprints as `""`, which never matches, so it is
    never counted as stuck. That is the deliberate direction to be wrong in: an
    agent writing prose might be summarising the work it just finished, and
    killing that is far worse than letting a stuck one spend one more round.
    """
    calls: list[str] = []
    for message in reversed(conv.messages):
        if message.role == "user":
            break  # everything above this belongs to an earlier turn
        if message.tool_name:
            args = json.dumps(message.tool_args or {}, sort_keys=True, ensure_ascii=False)
            calls.append(f"{message.tool_name}({args})")
    if not calls:
        return ""
    return hashlib.sha256("\n".join(sorted(calls)).encode()).hexdigest()[:16]


def last_human_message_ms(conv: Conversation) -> int | None:
    """When this chat's owner last said something, epoch ms — ignoring the
    driver's own rounds (`driven_by`), which are the agent talking to itself.

    Returns None when nobody has ever spoken, which reads as "not active"."""
    for message in reversed(conv.messages):
        if message.role == "user" and message.driven_by is None:
            return message.created_at
    return None


class OffHoursGoalSweeper:
    """Start one round per opted-in goal per off-hours stretch (see module doc)."""

    def __init__(
        self,
        spec: SpecStar,
        *,
        settings: OffHoursSettings,
        claims: SpecstarStretchClaims,
        start_round: Callable[[str], Awaitable[None]],
    ) -> None:
        from ..resources.work_calendar import register_work_calendar

        register_work_calendar(spec)  # the calendar is read on every tick
        self._spec = spec
        self._settings = settings
        self._claims = claims
        self._start_round = start_round

    async def tick(self, *, now: datetime | None = None) -> list[str]:
        """One sweep. Returns the conversation ids a round was started for."""
        calendar = build_offhours_calendar(self._spec, self._settings)
        if not calendar.enabled:
            return []
        moment = now if now is not None else datetime.now(UTC)
        if not calendar.is_offhours(moment):
            return []  # people are in the office
        stretch = calendar.stretch_id(moment)
        started: list[str] = []
        for cid, goal in self._eligible():
            if len(started) >= self._settings.max_concurrent:
                # Not an error, and not silent: the rest wait for the next tick
                # rather than all waking the moment the window opens.
                logger.info("goal offhours: concurrency cap reached, deferring the rest")
                break
            if self._owner_is_active(cid, moment):
                # Stand down COMPLETELY and give the claim back — better to run
                # late than to argue with the person we are working for.
                self._claims.release(cid)
                logger.debug("goal offhours: %s — owner active, standing down", cid)
                continue
            if not self._claims.try_claim(cid, stretch):
                continue  # a peer pod is driving it, or it already ran tonight
            logger.info(
                "goal offhours: starting %s for stretch %s (%d/%d rounds spent)",
                cid,
                stretch,
                goal.offhours_rounds_used,
                self._settings.max_rounds,
            )
            try:
                await self._start_round(cid)
            except Exception:
                # One chat's failure must not end the sweep for the fleet, and
                # must not cost that chat its night: give the claim back so a
                # later tick retries instead of waiting until tomorrow evening.
                logger.exception("goal offhours: starting %s failed", cid)
                self._claims.release(cid)
                continue
            started.append(cid)
        return started

    def _eligible(self) -> list[tuple[str, ConversationGoal]]:
        """Opted-in, still active, still in budget. Indexed on `offhours`, so
        this is a query over the few opted-in chats, not a scan of every goal."""
        rm = self._spec.get_resource_manager(ConversationGoal)
        out: list[tuple[str, ConversationGoal]] = []
        for r in rm.list_resources((QB["offhours"] == True).build()):  # noqa: E712
            goal = r.data
            assert isinstance(goal, ConversationGoal)
            if goal.state != "active":
                continue
            if goal.offhours_rounds_used >= self._settings.max_rounds:
                continue
            out.append((r.info.resource_id, goal))  # ty: ignore[unresolved-attribute]
        return out

    def _owner_is_active(self, conversation_id: str, now: datetime) -> bool:
        """Has a human said something in this chat within the yield window?"""
        rm = self._spec.get_resource_manager(Conversation)
        try:
            conv = rm.get(conversation_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):  # pragma: no cover
            return False
        assert isinstance(conv, Conversation)
        return owner_is_active(conv, now, self._settings)
