"""A turn's durable claim (plan-graceful-shutdown P3).

The question is persisted at acceptance (the 202 rule, `chat_send`); the turn
answering it is an `asyncio.Task` in one pod's memory. When that pod goes —
HPA scale-down, rollout, liveness kill, OOM — the task goes with it, nobody
else knows there was a turn to run, and the thread ends on the question for
ever. The claim is what a peer needs to run it instead: the whole send recipe
(which conversation, which message, who asked, the `_MessageBody` the routes
received — `apply_skills`, attached images and the retrieval knobs are NOT on
the persisted `Message`, and a re-run without them answers a different
question), plus who owns it and whether the owner let go on purpose.

One row per turn (id: engine key, the message's `created_at`, a unique
suffix); opened when the message is persisted, hard-deleted when the turn persists its
reply (`finish`), swept by the reclaimer when it lingers. NOT fields on the
`_TurnActivity` heartbeat row: that row's explicit "turn ended" write went
through three timing defects (see its module docstring) and was removed; a
per-turn row has no next turn to collide with, and liveness keeps coming from
the heartbeat, keyed the same way.

`take` is a CAS on the row's etag: two pods that see the same orphan on the
same tick both try, exactly one wins, the loser moves on.
"""

from __future__ import annotations

import abc
import contextlib
import logging
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from msgspec import Struct
from msgspec.structs import replace
from specstar import SpecStar
from specstar.types import (
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from .timeutil import now_ms

logger = logging.getLogger(__name__)


class TurnClaim(Struct):
    """The send recipe plus ownership. `body` is the `_MessageBody` as a plain
    dict (pydantic's `model_dump`).

    NOT on it: the request-composed env (#714). That is the caller's own
    cookie, a header their gateway stamped on — composed for one turn and, by
    `request_env.py`'s contract, never written back anywhere. A re-run is a
    turn nobody pressed send for, and asks the seam what such a turn gets."""

    key: str  # the engine key (item id for the default chat, else the chat id)
    created_at: int  # the persisted user message's `created_at` (ms)
    investigation_id: str
    rid: str  # the conversation id
    author: str
    lane: str = "background"
    body: dict[str, Any] = {}
    driven_by: str | None = None
    owner: str = ""  # pod id of whoever is running (or last ran) this turn
    released: bool = False  # the owner let go on purpose (a SIGTERM handover)
    reruns: int = 0  # times a peer has taken it over (the reclaimer's bound)
    taken_at_ms: int = 0  # when a peer last took it (0: never); see the reclaimer


def claim_id(key: str, created_at: int) -> str:
    """Readable prefix for the log, unique by the suffix: two sends on one
    key inside one millisecond are two questions, each owed an answer, and a
    row keyed by (key, created_at) alone merged them — the first turn's
    persist finished the shared row and the second's answer was never
    persisted."""
    return f"{key}:{created_at}:{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class ClaimRow:
    """A claim as read, with the etag `take` needs for its CAS."""

    id: str
    claim: TurnClaim
    etag: str


class ITurnClaimStore(abc.ABC):
    @abc.abstractmethod
    def open(self, claim: TurnClaim) -> str:
        """Record the turn as owned by THIS pod. Returns the row id."""

    @abc.abstractmethod
    def finish(self, cid: str) -> None:
        """The turn persisted its reply: the claim is done. Idempotent."""

    @abc.abstractmethod
    def release(self, keys: Collection[str]) -> None:
        """Let go of every open claim THIS pod holds on `keys` — a handover,
        not an end. One listing for the whole list (plus a write per claim
        released): a drain calls it once per engine with unfinished work.
        A claim a peer already took on one of those keys is the peer's. A
        release that lands LATE — after the drain stopped waiting for it and
        cancelled — loses or duplicates no answer (round 3 enumerated the end
        states, round 4 the interleavings): a copy that persisted has
        finished its claim (the row is gone, the write is a no-op); one whose
        persist failed, or a queued turn never started, is thereby handed to
        a peer, which is what was wanted; one a peer took meanwhile fails the
        CAS. Two windows, both inside the cost the reclaimer already accepts:
        a release landing between a copy's `is_mine` read and its `finish`,
        with a peer's tick inside those few ms, costs that peer one wasted
        re-run (the copy's own reply stands); and a copy whose reply persisted
        but whose `finish` was REFUSED keeps its row, so a late release hands
        it to a peer now rather than after the stale window — the duplicate
        answer that shape produces either way. So there is no deadline on
        it."""

    @abc.abstractmethod
    def list_open(self) -> list[ClaimRow]:
        """Every claim not yet finished, any owner."""

    @abc.abstractmethod
    def take(self, row: ClaimRow) -> ClaimRow:
        """Become the owner of `row`, by CAS on its etag. Raises
        `PreconditionFailedError` when another pod took it first."""

    @abc.abstractmethod
    def is_mine(self, cid: str) -> bool:
        """Still this pod's turn to answer: owned here and not let go of. What
        a turn asks before it persists — a claim a peer took (the epoch will
        have cancelled this copy) or this pod released (a handover) must not
        write its partial reply and cancel marker over the peer's answer."""


def register_turn_claims(spec: SpecStar) -> None:
    """Idempotent; post-apply in `create_app` like the other coordination rows,
    so no CRUD routes are emitted and the blob-gc worker's registry matches."""
    with contextlib.suppress(ValueError):
        spec.add_model(TurnClaim)


class SpecstarTurnClaimStore(ITurnClaimStore):
    def __init__(self, spec: SpecStar, *, pod_id: str) -> None:
        self._spec = spec
        self._pod_id = pod_id

    @property
    def pod_id(self) -> str:
        return self._pod_id

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not worth naming here
        return self._spec.get_resource_manager(TurnClaim)

    def open(self, claim: TurnClaim) -> str:
        cid = claim_id(claim.key, claim.created_at)
        rec = replace(claim, owner=self._pod_id, released=False)
        self._rm().create(rec, resource_id=cid, status=RevisionStatus.draft)
        return cid

    def finish(self, cid: str) -> None:
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            self._rm().permanently_delete(cid)

    def release(self, keys: Collection[str]) -> None:
        wanted = set(keys)
        for row in self.list_open():
            claim = row.claim
            if claim.key not in wanted or claim.released or claim.owner != self._pod_id:
                continue
            # A CAS on the etag the listing read: a peer's `take` that landed
            # in between must not be written over with this pod as owner
            # (the taker would then drop its answer as "not mine").
            try:
                self._rm().modify(
                    row.id,
                    replace(claim, released=True),
                    status=RevisionStatus.draft,
                    expected_etag=row.etag,
                )
            except PreconditionFailedError:
                continue  # a peer took it meanwhile: theirs
            except Exception:  # noqa: BLE001 — a handover that fails leaves the old behaviour, said so
                logger.warning("turn-claims: could not release %s", row.id, exc_info=True)

    def list_open(self) -> list[ClaimRow]:
        out: list[ClaimRow] = []
        for res in self._rm().list_resources():
            data = res.data
            assert isinstance(data, TurnClaim)
            out.append(ClaimRow(id=res.info.resource_id, claim=data, etag=res.info.etag))
        return out

    def is_mine(self, cid: str) -> bool:
        try:
            data = self._rm().get(cid).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return False
        assert isinstance(data, TurnClaim)
        return data.owner == self._pod_id and not data.released

    def take(self, row: ClaimRow) -> ClaimRow:
        rm = self._rm()
        taken = replace(
            row.claim,
            owner=self._pod_id,
            released=False,
            reruns=row.claim.reruns + 1,
            taken_at_ms=now_ms(),
        )
        info = rm.modify(
            row.id,
            taken,
            status=RevisionStatus.draft,
            expected_etag=row.etag,
        )
        return ClaimRow(id=row.id, claim=taken, etag=info.etag)
