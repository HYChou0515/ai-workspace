"""Take over the turns of a pod that is gone (plan-graceful-shutdown P3).

A turn is an `asyncio.Task` in one pod; its question is in the store. When the
pod goes, the thread ends on the question and nobody knows a reply is owed —
the FE reads "last message is the user's" (#559) and waits. `ReclaimTick` is
the other pod's side of the handover: it lists the open claims
(`turn_claims`), decides per KEY whether their owner is gone, takes them (a
CAS each, so one taker per claim fleet-wide), and re-runs each recipe on this
pod's engine (`ChatSendService.rerun`). The reply then persists and finishes
the claim exactly as a first run would.

The decision, per key (one conversation's queue):

- a claim on the key that is `released` — its owner let go on purpose (its
  drain, P4) and has already cancelled its copy: take it, now.
- the other claims on the key, while its heartbeat is fresh (`_TurnActivity`,
  30 s): someone is driving this conversation; leave them.
- the other claims once the heartbeat is stale — the owner died, or merely
  STALLED (loop wedged, not dead): take them too, advance the shared cancel
  epoch ONCE and BEFORE any re-run, so a stalled owner's copy cancels itself
  when it comes back (#349's watcher) and this pod's own re-runs, which stamp
  the epoch as they start, are not cancelled by it. Then re-run everything
  taken, in the order asked. (Released and stale on one key are taken on the
  same tick: taking only the released one had the other wait for that
  re-run's beat to go stale — answered second, half a minute late.)

A key is taken WHOLE or left whole. Two ticks can list the same key (the
lease serialises windows, and a window's end overlaps the next one's start);
each takes in the same order, so the first `take` to lose its CAS says a
peer is on this key, and this tick leaves the rest of it to that peer. Split
between two takers, both advanced the epoch and whichever landed second
cancelled the other's legitimate re-run through Stop's path — partial,
"interrupted", claim finished, question lost. A claim past its re-run bound
is likewise given up on by whoever TAKES it, so the thread gets one ending.

The claim is the ledger and the ONLY evidence: it is finished when the reply
persists and not before. Nothing about the thread's shape says whether a
given question was answered — a queued follow-up (Q1, Q2, A1, A2 is the
documented order), the #624 notice, a peer's answer to a LATER question all
sit after a question that is still unanswered, and a rule that read the
thread dropped the claim in every one of those shapes. The one thing such a
rule protected against — a pod dying between the reply's write and the
claim's delete, a millisecond apart — costs a duplicate answer; the rule cost
the answer. What bounds the re-runs instead is `RECLAIM_MAX_RERUNS`: a turn
that cannot finish anywhere is not re-run for ever, and the person is not
left waiting for ever either — the thread gets an error ending that says so.

One producer per window across the fleet (`ScanLease`, like every other
sweep), ticking every few seconds so a handover costs seconds, not the 30 s a
heartbeat takes to go stale. The table it lists is the claims in flight plus
whatever a crash left behind — bounded by concurrent turns, not by content.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from specstar.types import (
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from specstar import SpecStar

    from ..turn_control.base import ITurnControl
    from .chat_send import ChatSendService
    from .turn_activity import ITurnActivityStore
    from .turn_claims import ClaimRow, ITurnClaimStore

logger = logging.getLogger(__name__)

RECLAIM_TICK_S = 5.0
# Times a claim may be taken over before it is given up on. Two: one takeover
# is the ordinary handover, a second covers the taker itself being rolled; a
# third orphaning of the same question is a turn that fails wherever it runs.
RECLAIM_MAX_RERUNS = 2


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
        """One pass over the open claims, a key at a time. Returns the keys
        taken (for tests and the log). Per-key resilient: one bad conversation
        must not cost the rest."""
        taken: list[str] = []
        rows = await asyncio.to_thread(self.claims.list_open)
        by_key: dict[str, list[ClaimRow]] = {}
        # The same order on every pod, so two ticks on one key collide on its
        # FIRST claim and the loser leaves the whole key alone.
        for row in sorted(rows, key=lambda r: (r.claim.created_at, r.id)):
            by_key.setdefault(row.claim.key, []).append(row)
        for key, group in by_key.items():
            try:
                if await self._take_key(key, group):
                    taken.append(key)
            except Exception:  # noqa: BLE001 — logged, next key
                logger.exception("turn-reclaim: claims on %s could not be judged", key)
        return taken

    async def _take_key(self, key: str, group: list[ClaimRow]) -> bool:
        others = [row for row in group if not row.claim.released]
        stale = bool(others) and not await self.activity.alive(key)
        candidates = [row for row in group if row.claim.released or stale]
        if not candidates:
            return False
        mine: list[ClaimRow] = []
        stalled = False  # an unreleased claim was taken: its owner may still hold a copy
        for row in candidates:
            try:
                taken = await asyncio.to_thread(self.claims.take, row)
            except PreconditionFailedError:
                break  # a peer is taking this key: the rest of it is theirs
            except (ResourceIDNotFoundError, ResourceIsDeletedError):
                continue  # finished between the listing and now: the turn ended
            except Exception:  # noqa: BLE001 — this claim's problem, not the key's
                logger.exception("turn-reclaim: %s could not be taken; next tick", row.id)
                continue
            stalled = stalled or not row.claim.released
            if row.claim.reruns >= RECLAIM_MAX_RERUNS:
                await self._give_up(row)  # ours now, so ours to end — once
                continue
            logger.info(
                "turn-reclaim: taking over %s (released=%s, was %s, rerun %d)",
                row.id,
                row.claim.released,
                row.claim.owner,
                taken.claim.reruns,
            )
            mine.append(taken)
        if stalled:
            # Once per key, before any re-run: a stalled-not-dead owner still
            # holds a copy and the epoch is what makes it let go — also when
            # the claim was given up on rather than re-run, or that copy runs
            # to its end and only then finds its claim gone. Each re-run below
            # stamps the epoch as it starts, so an advance AFTER one would
            # cancel it too — the second claim's take used to do that to the
            # first claim's re-run.
            await self.control.advance(key)
        for row in mine:
            await self.chat_send.rerun(row)
        return bool(mine)

    async def _give_up(self, row: ClaimRow) -> None:
        logger.warning(
            "turn-reclaim: %s was taken over %d times without finishing; giving up",
            row.id,
            row.claim.reruns,
        )
        await self.chat_send.abandon(row)
