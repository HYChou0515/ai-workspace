"""One refresh at a time, across pods (`docs/plan-env-token-refresh.md`).

A system that rotates its refresh token accepts each one ONCE: two runs that
both find a token near expiry and both send its refresh token leave the second
with a rejection — and, written back, possibly the first's good values
overwritten by nothing. So each (layer, owner, provider) has one claim row,
taken by create-if-absent or, when the holder's time is up, by a CAS on the
row's etag; the loser waits and re-reads the layer the winner writes to.

The claim expires (`expires_ms`): a holder that dies mid-refresh must not
block that key for ever. Release is a CAS too and only by the holder, so a
late release from a taker whose claim already passed to another frees
nothing of theirs.

Registered after `spec.apply` like the other coordination rows: no CRUD
routes, and the blob-gc worker's registry matches the API's.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from urllib.parse import quote

from msgspec import Struct
from msgspec.structs import replace
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from .timeutil import now_ms


class RefreshClaim(Struct):
    key: str
    holder: str
    expires_ms: int


def register_refresh_claims(spec: SpecStar) -> None:
    """Idempotent; post-apply in `create_app`."""
    with contextlib.suppress(ValueError):
        spec.add_model(RefreshClaim)


class RefreshClaims:
    def __init__(self, spec: SpecStar, *, now: Callable[[], int] = now_ms) -> None:
        self._spec = spec
        self._now = now

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not worth naming here
        return self._spec.get_resource_manager(RefreshClaim)

    def try_take(self, key: str, *, ttl_ms: int) -> str | None:
        """The holder token when THIS caller now holds `key`, else None."""
        holder = uuid.uuid4().hex
        row = RefreshClaim(key=key, holder=holder, expires_ms=self._now() + ttl_ms)
        rid = quote(key, safe="")
        rm = self._rm()
        try:
            rm.create(row, resource_id=rid, status=RevisionStatus.draft, if_not_exists=True)
            return holder
        except DuplicateResourceError:
            pass
        try:
            res = rm.get(rid)
        except (
            ResourceIDNotFoundError,
            ResourceIsDeletedError,
        ):  # pragma: no cover - released between the two calls
            return None
        current = res.data
        assert isinstance(current, RefreshClaim)
        if current.expires_ms > self._now():
            return None  # someone holds it, and their time is not up
        try:
            rm.modify(rid, row, status=RevisionStatus.draft, expected_etag=res.info.etag)
        except PreconditionFailedError:  # pragma: no cover - cross-pod race
            return None  # another taker got the expired claim first
        return holder

    def held(self, key: str) -> bool:
        """Whether someone holds `key` now — a waiter's cue that the holder
        has let go: it reads the layer once more, then stops waiting."""
        try:
            res = self._rm().get(quote(key, safe=""))
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return False
        current = res.data
        assert isinstance(current, RefreshClaim)
        return current.expires_ms > self._now()

    def release(self, key: str, holder: str) -> None:
        """Free `key` if `holder` still holds it; otherwise nothing."""
        rid = quote(key, safe="")
        rm = self._rm()
        try:
            res = rm.get(rid)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return
        current = res.data
        assert isinstance(current, RefreshClaim)
        if current.holder != holder:
            return  # it passed to someone else: theirs
        with contextlib.suppress(PreconditionFailedError):  # taken over meanwhile: theirs
            rm.modify(
                rid,
                replace(current, expires_ms=0),
                status=RevisionStatus.draft,
                expected_etag=res.info.etag,
            )
