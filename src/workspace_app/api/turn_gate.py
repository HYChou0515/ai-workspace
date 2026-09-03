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

from collections.abc import Callable
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


def quota_code(exc: Exception) -> str:
    """The FE-facing code for one refusal."""
    return _CODES[type(exc)]


def quota_body(
    exc: Exception,
    *,
    viewer: str | None = None,
    titles_of: Callable[[list[str]], dict[str, str]] | None = None,
) -> dict[str, object]:
    """The 507 body for one refusal — the code plus the numbers behind it.

    The numbers are not decoration: "your workspace is full" is unfalsifiable to
    the person reading it, and a wrong one is indistinguishable from a right one
    until someone reads the code.

    `holding` says WHICH environments are using the quota, so the remedy is one
    click rather than a page the person has to know exists. Defaulting to the
    App's ceiling made hitting the limit ordinary rather than exceptional, so
    this is the difference between a wall and a door.

    It is included ONLY for the person whose quota it is. A collaborator can hit
    the owner's ceiling, and naming the items would hand them that person's
    working set — titles included — to explain one refusal. They still learn why
    they were stopped; they do not learn what else that person is doing. Absent
    `viewer` means no caller vouched for who is asking, and the list is withheld
    rather than assumed safe.
    """
    if isinstance(exc, SandboxQuotaExceeded):
        mine = viewer is not None and viewer == exc.owner
        held = list(exc.holding) if mine else []
        # One batched lookup for the whole list rather than one per row: this
        # runs on the failure path inside an exception handler, and the call
        # count is the latency.
        titles = titles_of([h.item_id for h in held]) if titles_of and held else {}
        return {
            "error": _CODES[SandboxQuotaExceeded],
            "dimension": exc.dimension,
            "used": exc.used,
            "limit": exc.limit,
            # Every row, NAMED only where the reader may see it — `titles_of`
            # returns an empty title rather than dropping the row. Each of these
            # is charged to the reader (`held` is emptied above unless the
            # viewer is the debtor), so an environment they cannot name is still
            # one they must be able to close; dropping it left somebody at
            # "1 of 1 in use" with nothing to act on. The title is the
            # disclosure, and `owner` is a field anyone with write access can
            # point at somebody else (#687), so the title stays gated on the
            # READER's own `read_meta`.
            #
            # The membership test below therefore only survives for
            # `titles_of is None`, where nothing can be named at all.
            "holding": [
                {
                    "item_id": h.item_id,
                    "title": titles[h.item_id],
                    "cpu_cores": h.cpu_milli / 1000,
                    "memory_bytes": h.memory_bytes,
                }
                for h in held
                if h.item_id in titles
            ],
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
        # NAMES the others rather than counting them. A scheduled run is the one
        # surface with no frontend to read `also`, so its failure record is the
        # whole message a person gets — "(and 1 more)" tells them there is
        # something else and not what.
        super().__init__("; also: ".join([str(primary), *(str(e) for e in also)]))
        self.primary = primary
        self.also = also

    def body(
        self,
        *,
        viewer: str | None = None,
        titles_of: Callable[[list[str]], dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return {
            **quota_body(self.primary, viewer=viewer, titles_of=titles_of),
            "also": [quota_code(e) for e in self.also],
        }


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
    # …then EVERY disk rule. `ensure_room_for` raises the first, which is right
    # for a write that is stopping anyway; a refusal a person has to act on wants
    # all of them, or they fix one and meet the next on the retry.
    refusals.extend(await files.room_refusals(item_id, 1, record=False))

    if not refusals:
        return
    if len(refusals) == 1:
        raise refusals[0]
    raise TurnRefused(refusals[0], refusals[1:])
