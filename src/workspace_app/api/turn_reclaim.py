"""Take over the turns of a pod that is gone (plan-graceful-shutdown P3).

A turn is an `asyncio.Task` in one pod; its question is in the store. When the
pod goes, the thread ends on the question and nobody knows a reply is owed —
the FE reads "last message is the user's" (#559) and waits. `ReclaimTick` is
the other pod's side of the handover: it lists the open claims
(`turn_claims`), decides for each whether its owner is gone, takes it (a CAS,
so one taker per claim fleet-wide), advances the shared cancel epoch so a pod
that merely STALLED — loop wedged, heartbeat stale, not dead — cancels its own
copy when it comes back (#349's watcher), and re-runs the recipe on this pod's
engine (`ChatSendService.rerun`). The reply then persists and finishes the
claim exactly as a first run would.

The decision, per claim:

- `released` — the owner let go on purpose (its drain, P4): take it now.
- the key's heartbeat is fresh (`_TurnActivity`, 30 s): someone is driving;
  leave it.
- stale, and the thread still ends on that user message: the owner died
  mid-turn; take it.
- stale, and the thread has moved on (a reply, an error ending): the turn
  ended and only `finish` was lost — the pod died between the two writes.
  Delete the claim; re-running would answer twice.

One producer per window across the fleet (`ScanLease`, like every other
sweep), ticking every few seconds so a handover costs seconds, not the 30 s a
heartbeat takes to go stale. The table it lists is the claims in flight plus
whatever a crash left behind — bounded by concurrent turns, not by content.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from specstar.types import PreconditionFailedError

from ..resources import Conversation

if TYPE_CHECKING:
    from fastapi import FastAPI
    from specstar import SpecStar

    from ..turn_control.base import ITurnControl
    from .chat_send import ChatSendService
    from .turn_activity import ITurnActivityStore
    from .turn_claims import ClaimRow, ITurnClaimStore

logger = logging.getLogger(__name__)

RECLAIM_TICK_S = 5.0


@dataclass
class ReclaimTick:
    spec: SpecStar
    claims: ITurnClaimStore
    activity: ITurnActivityStore
    control: ITurnControl
    chat_send: ChatSendService

    @classmethod
    def of(cls, app: FastAPI) -> ReclaimTick:
        """From an app `create_app` composed — the same objects its routes use."""
        st = app.state
        return cls(
            spec=st.spec,
            claims=st.turn_claims,
            activity=st.turn_activity,
            control=st.turn_control,
            chat_send=st.chat_send,
        )

    async def run(self) -> list[str]:
        """One pass over the open claims. Returns the keys taken (for tests and
        the log). Per-claim resilient: one bad row must not cost the rest."""
        taken: list[str] = []
        rows = await asyncio.to_thread(self.claims.list_open)
        for row in rows:
            try:
                if await self._take_if_orphaned(row):
                    taken.append(row.claim.key)
            except Exception:  # noqa: BLE001 — logged, next claim
                logger.exception("turn-reclaim: claim %s could not be judged", row.id)
        return taken

    async def _take_if_orphaned(self, row: ClaimRow) -> bool:
        claim = row.claim
        if not claim.released:
            if await self.activity.alive(claim.key):
                return False
            if not await asyncio.to_thread(self._still_owed, claim.rid, claim.created_at):
                logger.info(
                    "turn-reclaim: %s ended without finishing its claim; dropping it", row.id
                )
                await asyncio.to_thread(self.claims.finish, row.id)
                return False
        try:
            mine = await asyncio.to_thread(self.claims.take, row)
        except PreconditionFailedError:
            return False  # a peer took it on the same tick
        # A stalled-not-dead owner still holds a copy: the epoch is what makes
        # it let go, so the thread gets ONE answer.
        await self.control.advance(claim.key)
        logger.info(
            "turn-reclaim: taking over %s (released=%s, was %s)",
            row.id,
            claim.released,
            claim.owner,
        )
        await self.chat_send.rerun(mine)
        return True

    def _still_owed(self, rid: str, created_at: int) -> bool:
        """Does the thread still end on the user message this claim is for?"""
        with contextlib.suppress(Exception):
            conv = self.spec.get_resource_manager(Conversation).get(rid).data
            if isinstance(conv, Conversation) and conv.messages:
                last = conv.messages[-1]
                return last.role == "user" and last.created_at == created_at
        return False
