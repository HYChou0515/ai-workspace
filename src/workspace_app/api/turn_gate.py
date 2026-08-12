"""One place that decides whether a turn may start, and one place that says why.

Both halves used to be duplicated. The CHECK was two lines copied into
`chat_send.send` and `workflow_exec.drive_turn`; the WORDING was a body literal
repeated per exception handler. Duplicated rules in this area have a history of
drifting apart, so they are stated once here.

The check runs BOTH gates rather than stopping at the first refusal. Running
them in a fixed order and reporting only the winner produced a sequence that
reads as a bug even though every message in it is true: someone out of disk AND
at their environment limit is told to delete files, deletes them, sends again,
and is told something different. The first message implied that acting on it
would let the turn through, and it would not have.

The environment leads when both bind: closing one is a single click on a page
the refusal links to, while freeing disk means going into the item and choosing
what to lose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..files.facade import WorkspaceFull
from ..quota.admission import SandboxQuotaExceeded
from ..quota.disk_ledger import UserDiskFull

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from ..files.facade import WorkspaceFiles
    from ..quota.admission import AdmissionGate

#: Every refusal that means "you are holding as much as you may hold". The code
#: is the FE's switch (`web/src/lib/quotaFailure.ts`) — three rules share 507 and
#: need three different remedies, so the status alone is never enough.
_CODES: dict[type[Exception], str] = {
    WorkspaceFull: "workspace_quota_exceeded",
    UserDiskFull: "user_quota_exceeded",
    SandboxQuotaExceeded: "sandbox_quota_exceeded",
}

QUOTA_REFUSALS = tuple(_CODES)


def quota_code(exc: Exception) -> str:
    """The FE-facing code for one refusal."""
    return _CODES[type(exc)]


def quota_body(exc: Exception) -> dict[str, object]:
    """The 507 body for one refusal — the code plus the numbers behind it.

    The numbers are not decoration: "your workspace is full" is unfalsifiable to
    the person reading it, and a wrong one is indistinguishable from a right one
    until someone reads the code."""
    if isinstance(exc, SandboxQuotaExceeded):
        return {
            "error": _CODES[SandboxQuotaExceeded],
            "dimension": exc.dimension,
            "used": exc.used,
            "limit": exc.limit,
        }
    assert isinstance(exc, WorkspaceFull | UserDiskFull)
    return {
        "error": quota_code(exc),
        "used": exc.used,
        "quota": exc.quota,
        "attempted": exc.attempted,
    }


class TurnRefused(Exception):
    """More than one limit is at its cap, so the refusal names them all.

    Wraps rather than replaces the individual refusals: one limit binding is by
    far the common case and keeps raising its own exception, so every existing
    handler, test and tool message is untouched."""

    def __init__(self, primary: Exception, also: list[Exception]) -> None:
        super().__init__(f"{primary} (and {len(also)} more limit(s) at their cap)")
        self.primary = primary
        self.also = also

    def body(self) -> dict[str, object]:
        return {**quota_body(self.primary), "also": [quota_code(e) for e in self.also]}


async def admit_turn(
    files: WorkspaceFiles,
    admission: AdmissionGate | None,
    item_id: str,
) -> None:
    """Refuse a turn BEFORE the user's message is persisted, naming every limit
    that is at its cap.

    Gating each write instead still let the whole turn run — the agent planned,
    wrote, was refused, retried, wrote elsewhere — so every instruction given to
    an already-full workspace burned a turn to rediscover the same thing.
    Refusing up front is also what keeps the composer from waiting on a reply
    that will never come; clearing space needs no agent, because deleting from
    the file tree is never quota-gated.

    Both gates run even once one has refused. That costs one extra measurement
    on a path that is already failing, and buys an answer the person can act on
    in a single pass."""
    refusals: list[Exception] = []
    # cpu/memory/count first: if this turn would need a NEW sandbox its owner may
    # not have, that is the refusal whose remedy is one click away.
    if admission is not None:
        try:
            await admission.check(item_id)
        except SandboxQuotaExceeded as exc:
            refusals.append(exc)
    try:
        await files.ensure_room_for(item_id, 1)
    except (WorkspaceFull, UserDiskFull) as exc:
        refusals.append(exc)

    if not refusals:
        return
    if len(refusals) == 1:
        raise refusals[0]
    raise TurnRefused(refusals[0], refusals[1:])
