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

One row per turn, keyed by the engine key and the message's `created_at`;
opened when the message is persisted, hard-deleted when the turn persists its
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
from dataclasses import dataclass
from typing import Any

from msgspec import Struct
from msgspec.structs import replace
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

logger = logging.getLogger(__name__)


class TurnClaim(Struct):
    """The send recipe plus ownership. `body` is the `_MessageBody` as a plain
    dict (pydantic's `model_dump`), `caller_env` the request-composed env the
    turn ran with (a re-run cannot ask a request that is gone)."""

    key: str  # the engine key (item id for the default chat, else the chat id)
    created_at: int  # the persisted user message's `created_at` (ms)
    investigation_id: str
    rid: str  # the conversation id
    author: str
    lane: str = "background"
    body: dict[str, Any] = {}
    caller_env: dict[str, str] = {}
    driven_by: str | None = None
    owner: str = ""  # pod id of whoever is running (or last ran) this turn
    released: bool = False  # the owner let go on purpose (a SIGTERM handover)


def claim_id(key: str, created_at: int) -> str:
    return f"{key}:{created_at}"


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
    def release(self, key: str) -> None:
        """Let go of every open claim on `key` — a handover, not an end."""

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
        rm = self._rm()
        try:
            rm.create(rec, resource_id=cid, status=RevisionStatus.draft)
        except DuplicateResourceError:
            # The same message re-sent into the store (a retry): ours again.
            rm.modify(cid, rec, status=RevisionStatus.draft)
        return cid

    def finish(self, cid: str) -> None:
        with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
            self._rm().permanently_delete(cid)

    def release(self, key: str) -> None:
        for row in self.list_open():
            if row.claim.key != key or row.claim.released:
                continue
            with contextlib.suppress(Exception):
                self._rm().modify(
                    row.id, replace(row.claim, released=True), status=RevisionStatus.draft
                )

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
        taken = replace(row.claim, owner=self._pod_id, released=False)
        info = rm.modify(
            row.id,
            taken,
            status=RevisionStatus.draft,
            expected_etag=row.etag,
        )
        return ClaimRow(id=row.id, claim=taken, etag=info.etag)
