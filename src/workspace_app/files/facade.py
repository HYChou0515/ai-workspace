"""WorkspaceFiles — the single chokepoint for workspace file access.

It routes by **sandbox liveness**: when a sandbox dir is live for the workspace
(the single source of truth), reads/writes go there; when it's cold/recycled,
they fall back to the durable FileStore snapshot.

#345: with a shared per-item dir on one volume, the handle is derivable on ANY
pod (not just the one that woke the sandbox), so `_warm` PROBES the dir and
falls back to the snapshot only on `SandboxNotFound` (the dir is cold). That's
what keeps a read on a non-owning pod consistent with the live dir instead of
serving a stale snapshot — so workspace data no longer depends on sticky routing.

`is_dir`/`listdir` are derived from `walk` when warm (the Sandbox Protocol has
no native dir listing); cold, they read the FileStore which tracks dirs
first-class. Constructed without a sandbox (`sandbox=None`), it degrades to a
plain FileStore pass-through — handy for tests + the transitional fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from ..filestore.protocol import FileExists, FileNotFound, FileStore
from ..quota.disk_ledger import UserDiskFull
from ..sandbox.protocol import Sandbox, SandboxBusy, SandboxHandle, SandboxNotFound


class PersonDiskGate(Protocol):
    """The per-person disk gate.

    `record` is part of the CONTRACT, not an optimisation: the gate writes the
    post-write size to the ledger when it ALLOWS a write, so a caller that is
    only asking has to be able to say so.

    A Protocol with `__call__` rather than a `Callable[...]` alias, because the
    alias could not express a keyword-only argument — and `Callable[..., X]`
    accepts ANY parameter list, so widening to it silently switched off the
    arity check that was there before. `ty` then passed a gate taking a single
    `bytes`. On a branch where a test double had already fallen behind this
    contract twice, deleting the one mechanical check was the wrong trade."""

    def __call__(
        self, workspace_id: str, new_size: int, growth: int, /, *, record: bool = True
    ) -> Awaitable[None]: ...

    # Not `async def`: a plain `async def` function is `(...) -> Coroutine`, and
    # declaring the protocol member async would demand a class with an async
    # `__call__` instead. Positional-only (`/`) so a caller may name the first
    # three whatever reads best at their call site — the KEYWORD one is the part
    # that has to match, because that is the part carrying the contract.


# How many times an etag-guarded edit re-bases against a concurrent writer
# before giving up and reporting a conflict. A handful is plenty — contention
# on one wiki page across workers is rare and each retry re-reads fresh.
logger = logging.getLogger(__name__)

# How many times an etag-guarded edit re-bases…
_CAS_EDIT_RETRIES = 5

# #538: how long a warm workspace's measured file sizes stay usable before the
# sandbox is walked again. Matches `create_app`'s default `mirror_interval` —
# the workspace is already reconciled to the durable snapshot on that cadence,
# so the quota gains nothing from a finer one, and a user-visible number that
# trails reality by at most one such window is what the rest of the system
# already promises.
_USAGE_WINDOW_S = 5.0


class _Measurement:
    """A workspace's size, and when it was taken.

    Just a number now. It used to carry a `path -> size` map as well, because
    the quota also needs the size of the file being overwritten — but that map
    was one file-tree-sized dict per warm item, held in THIS process, and the
    two bugs it caused (a leak, twice, and an O(n) re-sum per write) both came
    from keeping a copy of the sandbox here. `size_of` answers the per-file
    half from the sandbox instead, so both halves come from the same live
    source without either being mirrored into app memory."""

    __slots__ = ("at", "total")

    def __init__(self, at: float, total: int) -> None:
        self.at = at
        self.total = total


class WorkspaceFull(Exception):
    """A write was refused because it would push the workspace past its quota
    (#538). Raised by the facade, so every write path — an upload, an IDE save,
    the agent's own `write_file`, a workflow — is refused by the same rule
    rather than only the one endpoint that happened to check.

    Carries the numbers the caller needs to tell the user what to do about it:
    the API turns them into a 507 body, the agent tools into a message that says
    to delete something."""

    def __init__(self, used: int, quota: int, attempted: int) -> None:
        super().__init__(
            f"workspace is full: {used} of {quota} bytes used, cannot write {attempted} more"
        )
        self.used = used
        self.quota = quota
        self.attempted = attempted


def abs_path(path: str) -> str:
    """Canonicalise a workspace path: ``./brief.md``, ``brief.md`` and
    ``/brief.md`` all map to the same internal key ``/brief.md``. So
    the agent can write whichever feels natural in prose and the
    underlying store stays consistent.

    ``rel_path``'s counterpart: this is the form every non-model surface wants
    (the store key, a fetch URL, the file-tree opener)."""
    p = path.removeprefix("./")
    return p if p.startswith("/") else "/" + p


def rel_path(path: str) -> str:
    """`abs_path`'s inverse — the workspace path as an AGENT should ever see it.

    The store's key is absolute-looking (`/brief.md`) and the file tools take it
    back happily, but `exec` runs a real process whose cwd is the workspace and
    which has no chroot: there, `/brief.md` is the *system* root. Any path we put
    in front of a model — a listing, a grep hit, a prompt, a tool's confirmation
    — therefore goes through here, so the model only ever learns the one form
    that works in every surface it can use a path in. Input stays permissive;
    this is about what we TEACH, not what we accept."""
    return path.lstrip("/")


def _dir_key(path: str) -> str:
    """`path` as a directory key: `""` for the workspace root, else `/a/b`.

    `.` and `./` mean the root here for the same reason `./x` means `/x` in
    `abs_path` — the agent is told the three spellings are interchangeable, so a
    bare `.` must not resolve to a directory literally named `.`."""
    p = path.strip()
    if p in ("", ".", "./", "/"):
        return ""
    return abs_path(p).rstrip("/")


def _split_level(entries: list[str], prefix: str) -> tuple[list[str], list[str]]:
    """Split recursive `entries` into (files, dirs) at one level below `prefix`."""
    files: list[str] = []
    dirs: set[str] = set()
    for entry in entries:
        if not entry.startswith(prefix):
            continue
        rest = entry[len(prefix) :]
        if not rest:
            continue
        head, slash, _ = rest.partition("/")
        if slash:
            dirs.add(prefix + head + "/")
        else:
            files.append(entry)
    return sorted(files), sorted(dirs)


def _dirs_of(paths) -> list[str]:
    """Every ancestor directory implied by a set of file paths, sorted."""
    dirs: set[str] = set()
    for path in paths:
        parts = path.strip("/").split("/")
        for i in range(1, len(parts)):
            dirs.add("/" + "/".join(parts[:i]))
    return sorted(dirs)


class WorkspaceFiles:
    def __init__(
        self,
        filestore: FileStore,
        sandbox: Sandbox | None = None,
        handle_for: Callable[[str], Awaitable[SandboxHandle | None]] | None = None,
        rebuild: Callable[[str], Awaitable[SandboxHandle]] | None = None,
        quota: int | Callable[[str], int] = 0,
        person_gate: PersonDiskGate | None = None,
        on_usage: Callable[[str, int], Awaitable[None]] | None = None,
        usage_window: float = _USAGE_WINDOW_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fs = filestore
        self._sb = sandbox
        # #538: bytes one workspace may occupy; 0 ⇒ unlimited (the default, so the
        # wiki-page stores and other non-workspace uses are never gated).
        #
        # A workspace's limit belongs to its App, so the real deployment passes a
        # LOOKUP. A plain int is still accepted and means "the same number for
        # every workspace" — it normalises to a lookup right here, so internally
        # there is exactly ONE way to ask, and no pair of spellings that could
        # ever answer differently.
        self._quota_for: Callable[[str], int]
        if isinstance(quota, int):
            flat = quota  # bound now, so the lambda cannot capture a later value
            self._quota_for = lambda _ws: flat
        else:
            self._quota_for = quota
        # The SECOND gate: this workspace's owner may also have a total across
        # every item they own. Injected as a callback rather than implemented
        # here — the facade knows about workspaces, not about people — and only
        # consulted when a write actually GROWS the workspace, so the per-person
        # rule inherits the same "shrinking and deleting always pass" guarantee
        # without restating it. Called as (workspace_id, its new size, growth).
        self._person_gate = person_gate
        # Publishes a workspace's size to whoever keeps the durable per-person
        # total. Fired on DELETE — the act of someone at their cap trying to get
        # back under it, which must be believed immediately rather than at the
        # next sweep (#538's "clear the workspace, still be told you are out of
        # space", in its cross-item form). Growth deliberately does NOT publish:
        # the gate measures the item being written live, so its own number is
        # already exact, and the sweep refreshes the row anyway — charging every
        # write a durable round-trip would buy nothing.
        self._on_usage = on_usage
        # Async resolver: item → the handle its ONE live sandbox is reachable at,
        # or None when the item is globally cold (#492 same-source resolution).
        self._handle_for = handle_for
        # Async rebuild: item → a FRESH live handle when the resolved one turns out
        # reaped. Wired ONLY for a host-managed-durable (http) backend, where a
        # reaped-but-globally-warm item must NOT fall back to a cold durable write
        # (the host's `--delete` mirror would reconcile it away). None ⇒ the local
        # shared-vol backend, whose durable is the FileStore snapshot with no
        # host-side reconcile, so a cold dir safely falls back to durable (#345).
        self._rebuild = rebuild
        # Per-(workspace, path) lock so a compare-and-swap (read → check →
        # write) is atomic against other writers going through this facade.
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        # #538: workspace → (measured_at, path → size) for a WARM workspace. The
        # quota is measured from the live sandbox, and measuring means walking
        # it; a folder upload asks once per file, so an unmemoised walk would
        # make an N-file batch cost N traversals. Re-walked at most once per
        # `usage_window` (the mirror interval — the same granularity the rest of
        # the system already reconciles at), while writes and deletes made
        # THROUGH this facade patch the map directly, so a batch stays exact
        # without re-walking. Bytes that appear behind our back (the shell, a
        # download) are picked up on the next window.
        self._window = usage_window
        self._now = now
        self._tree: dict[str, _Measurement] = {}
        # One walk per workspace at a time. Without this, two coroutines that
        # both miss the memo both walk, and whichever finishes LAST installs its
        # map — silently discarding any write the other recorded in between, so
        # the workspace under-counts for the rest of the window by however many
        # writes raced (a `gather` over several artifacts, say).
        self._walk_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _warm(self, workspace_id: str) -> tuple[Sandbox, SandboxHandle] | None:
        """The item's ONE live sandbox, or None when it is globally cold (so the
        op uses the durable store). Reads AND writes route through here, so both
        hit the SAME source (#492) — a write never lands somewhere a later read
        wouldn't see, and never in a cold durable copy the host would reconcile
        away while a live sandbox exists.

        `handle_for` resolves the handle GLOBALLY (this pod's session / the shared
        address / the id-derived shared dir); None means globally cold (¬P) → the
        durable store. A resolved handle is probed for liveness:

        - alive ⇒ route the op to it.
        - `SandboxNotFound` (reaped/gone) with a `rebuild` wired (http) ⇒ rebuild
          from the durable archive and route to the fresh sandbox — NOT the cold
          durable store (the item is globally warm; a cold write would be lost).
        - `SandboxNotFound` with no rebuild (local shared-vol) ⇒ the shared dir is
          cold ⇒ fall back to the durable snapshot, as before (#345).
        - `SandboxBusy` (reachable but slow) propagates: the http client already
          retried with an escalating deadline, so this fails loud rather than
          rebuilding a live sandbox (split-brain) or cold-writing (data loss)."""
        if self._sb is None or self._handle_for is None:
            return None
        handle = await self._handle_for(workspace_id)
        if handle is None:
            return None
        # Probed on EVERY op, deliberately. Caching the answer looks free — a
        # user action issues several ops and they all ask the same question —
        # but this probe is also the RECOVERY trigger: `SandboxNotFound` here is
        # what rebuilds a sandbox the host reaped or lost to a pod restart, and
        # the ops themselves do not catch it. Serving a remembered "alive" turns
        # that recovery into a 500 for as long as the memory lasts. Measured, the
        # memo saved two probes per file save once the surrounding operation
        # counts came down — not a trade worth a live error path.
        try:
            await self._sb.exists(handle, "/")  # SandboxNotFound = gone; SandboxBusy propagates
        except SandboxNotFound:
            if self._rebuild is None:
                return None  # local shared-vol cold dir → durable snapshot (#345)
            handle = await self._rebuild(workspace_id)  # http: reaped but warm → rebuild
        return (self._sb, handle)

    #: How many paths one batched download may ask for at a time. A workspace
    #: can hold thousands of files, and a single request for all of them is a
    #: different failure (a huge body, a connection held open for it) from the
    #: one batching fixes. A constant rather than a config option on purpose:
    #: it is an internal chunking detail with no behaviour an operator could
    #: reason about, and every knob is a row somebody has to keep in the
    #: migration ledger.
    _BATCH_PATHS = 200

    async def read(self, workspace_id: str, path: str) -> bytes:
        return await self._read_with(workspace_id, path, await self._warm(workspace_id))

    # NOTE: the module-level `read_all` below is how callers reach this — it
    # degrades for stores that do not have it. Do not duck-type it a second
    # time at a call site; two spellings of one rule drift.

    async def read_many(self, workspace_id: str, paths: Sequence[str]) -> list[bytes]:
        """Read several paths as ONE operation — liveness resolved once, every
        path read against that same answer, in the order given.

        Calling `read` per path re-resolves it per path, and each resolution is
        a liveness probe: against the hosted sandbox that is a second network
        round trip in front of every single file. Measured on the entity
        listings, which read a whole record type at a time: a 68-issue listing
        cost ~150 round trips where ~70 would do, and a milestone listing —
        rolling up every issue — cost MORE to return 7 records than the issue
        listing did to return 68.

        This is the `_read_with` contract the multi-step writers already use
        (resolve once, every step provably hits the same store), NOT a memo of
        the probe's answer: `_warm` is also the recovery trigger for a sandbox
        the host reaped, so remembering "alive" across operations turns that
        recovery into a 500. One operation, one resolution.

        A sandbox that can hand back many files at once takes the fast lane
        below; one that cannot is read a file at a time, and NOTHING above here
        can tell which happened — same bytes, same order, same errors, only a
        different number of round trips."""
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, handle = warm
            batched = getattr(sb, "download_many", None)
            if batched is not None:
                return await self._download_batched(batched, handle, paths)
        elif (cold := getattr(self._fs, "read_many", None)) is not None:
            # A workspace with no live sandbox is answered by the durable store,
            # where the round trips are to the database — the sandbox's batch
            # does nothing for it, so the store has a batch of its own.
            return list(await cold(workspace_id, [abs_path(p) for p in paths]))
        return [await self._read_with(workspace_id, path, warm) for path in paths]

    async def _download_batched(
        self,
        batched: Callable[[SandboxHandle, list[str]], Awaitable[Sequence[bytes | None]]],
        handle: SandboxHandle,
        paths: Sequence[str],
    ) -> list[bytes]:
        """The fast lane: whole chunks of paths per round trip.

        `None` in the answer means THAT PATH is absent — an answer, not a failed
        batch — so the miss raises exactly what reading it alone would, and the
        tolerant `read_all_existing` keeps skipping it. Anything else would make
        one deleted file the difference between a listing and an error page."""
        out: list[bytes] = []
        for start in range(0, len(paths), self._BATCH_PATHS):
            chunk = [abs_path(p) for p in paths[start : start + self._BATCH_PATHS]]
            answers = await batched(handle, chunk)
            for path, blob in zip(chunk, answers, strict=True):
                if blob is None:
                    raise FileNotFound(path)
                out.append(blob)
        return out

    async def _read_with(self, workspace_id: str, path: str, warm) -> bytes:
        """`read` against an ALREADY-resolved liveness. Split out so a multi-step
        operation resolves once and every step provably hits the same store."""
        path = abs_path(path)
        if warm is not None:
            sb, h = warm
            try:
                return await sb.download(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        return await self._fs.read(workspace_id, path)

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        previous = await self._ensure_headroom(workspace_id, path, len(data), warm)
        await self._write_unchecked(workspace_id, path, data, warm, previous)

    async def _write_unchecked(
        self,
        workspace_id: str,
        path: str,
        data: bytes,
        warm: tuple[Sandbox, SandboxHandle] | None,
        previous: int | None = None,
    ) -> None:
        """The write itself, without the quota gate — for callers that have
        already established the operation cannot grow the workspace.

        `previous` is what the path held before, when the caller happens to know
        it (the gate looked it up). Knowing it keeps the measurement exact across
        a batch; not knowing it drops the measurement so the next read is honest
        rather than guessed."""
        if warm is not None:
            sb, h = warm
            await sb.upload(h, data, path)
        else:
            await self._fs.write(workspace_id, path, data)
        if previous is None:
            self._forget(workspace_id)
        else:
            self._adjust(workspace_id, len(data) - previous)

    async def move(self, workspace_id: str, src: str, dst: str) -> None:
        """Relocate one file. **Not** quota-gated, and deliberately so: the bytes
        land under a new name and leave the old one, so the workspace's size is
        unchanged (#538).

        Gating it per-write would mean a rename needs headroom for a second copy
        of the file — so renaming anything in a workspace that is more than half
        full would be refused, and renaming a folder would need room for the
        whole tree. Worse, the rename a user reaches for to tidy up is exactly
        the operation an over-quota workspace must not refuse. The source is
        removed immediately after the destination lands; if that removal fails
        the destination is rolled back, so a failed move leaves the workspace
        exactly as it was — never a hole, and never a duplicate the user has to
        reconcile against a 500 (#588)."""
        src, dst = abs_path(src), abs_path(dst)
        # Resolve liveness ONCE for the whole operation (#588). Each step used to
        # resolve it independently, so a sandbox reaped or rebuilt mid-move
        # (#345/#366) could send the read and the delete to DIFFERENT stores —
        # which is how the delete came to fail on a file the read had just
        # returned, intermittently and only under real sandbox churn.
        warm = await self._warm(workspace_id)
        data = await self._read_with(workspace_id, src, warm)
        await self._write_unchecked(workspace_id, dst, data, warm)
        try:
            await self._delete_with(workspace_id, src, warm)
        except Exception:
            # The source would not go, so the move FAILED — make it look like it
            # never ran. Keeping the destination (the old behaviour, described in
            # this docstring as "a harmless duplicate") hands the user a 500 AND
            # two copies to sort out by hand. The destination is provably free
            # before a move (the route 409s on an occupied target), so removing
            # what we just wrote cannot destroy anything else.
            with contextlib.suppress(Exception):
                await self._delete_with(workspace_id, dst, warm)
            raise

    async def write_record(self, workspace_id: str, path: str, data: bytes) -> None:
        """Write a record of something that has ALREADY happened. **Not** quota
        gated, and deliberately so (#538).

        A quota check is a precondition: it is only meaningful where the caller
        can still abandon the operation. This write comes AFTER the effect it
        describes — a workflow step that has already created its entities and
        sent its notifications — so refusing it undoes nothing. It only loses
        the record, and the re-run then finds no record and does the whole step
        again. Duplicated side effects are worse than a few dozen bytes of
        bookkeeping past the cap.

        Exemptions stay NAMED operations, like `move` (which cannot grow the
        workspace at all), rather than a flag any caller can pass — an
        ungated write anyone can reach for is not a quota."""
        path = abs_path(path)
        await self._write_unchecked(workspace_id, path, data, await self._warm(workspace_id))

    async def create_exclusive(self, workspace_id: str, path: str, data: bytes) -> None:
        """Create-if-absent (#419 N1 numbering arbiter): raise `FileExists` if
        `path` is taken, else create it. Cold ⇒ the durable store's atomic
        create-only (`SpecstarFileStore.create_exclusive`). Warm ⇒ exists-check +
        upload against the live sandbox; that pair isn't a single atomic op, but a
        warm sandbox is single-pod (§N5) so the caller's per-type lock already
        serialises claimants there — the durable path is where cross-pod atomicity
        matters, and it has it.

        The quota is checked only AFTER the name is found free: `FileExists` is
        an answer callers act on — `entity/store.py` walks to the next free
        number on it — so reporting "full" for a name that was taken anyway
        would abort a search that had nothing to do with space."""
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            if await sb.exists(h, path):
                raise FileExists(path)
            await self._ensure_headroom(workspace_id, path, len(data), warm)
            await sb.upload(h, data, path)
            self._adjust(workspace_id, len(data))  # proven absent above
            return
        if await self._fs.exists(workspace_id, path):
            raise FileExists(path)
        await self._ensure_headroom(workspace_id, path, len(data), warm)
        native = getattr(self._fs, "create_exclusive", None)
        if native is not None:
            await native(workspace_id, path, data)
            return
        await self._fs.write(workspace_id, path, data)

    async def write_from_path(
        self, workspace_id: str, path: str, source: Path, content_type: str | None = None
    ) -> None:
        """Like `write`, but the content is a staged on-disk file `source` that
        is streamed into its destination — so a big upload never sits whole in
        RAM (issue #219). Warm ⇒ stream straight into the live sandbox (the
        snapshot catches up on the next mirror, exactly like any warm write);
        cold ⇒ stream into the FileStore blob."""
        path = abs_path(path)
        # The streaming upload route also checks mid-stream so an over-quota body
        # is rejected before it's staged; this is the backstop that keeps the rule
        # true for any future caller that doesn't.
        warm = await self._warm(workspace_id)
        size = source.stat().st_size
        previous = await self._ensure_headroom(workspace_id, path, size, warm)
        if warm is not None:
            sb, h = warm
            await sb.upload_file(h, source, path)
        else:
            await self._fs.write_from_path(workspace_id, path, source, content_type)
        if previous is None:
            self._forget(workspace_id)
        else:
            self._adjust(workspace_id, size - previous)

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        """Like `read`, but stream the bytes out to the on-disk `dest` — RAM-free
        for big files (issue #219). Routes warm→sandbox / cold→snapshot like
        `read`; a missing file maps to `FileNotFound`."""
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.download_to_file(h, path, dest)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.read_to_file(workspace_id, path, dest)

    async def exists(self, workspace_id: str, path: str) -> bool:
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return await sb.exists(h, path)
        return await self._fs.exists(workspace_id, path)

    async def workspace_usage(self, workspace_id: str) -> int:
        """Total bytes the workspace's files occupy — the #245 quota basis,
        measured against the **live** workspace (#538).

        Routed warm/cold exactly like `stat_all`, because it has to answer the
        same question the file tree does: warm ⇒ summed from the sandbox's own
        `walk` (a stat, never a read — the same basis `registry._scratch_usage`
        uses); cold ⇒ the durable store's aggregate.

        Measuring the durable snapshot instead was the #538 bug. The snapshot
        only catches up on a mirror sweep, so it counted the wrong things in
        both directions: bytes the agent created in the sandbox (exec output,
        downloads) were invisible and therefore free, while bytes deleted in
        the sandbox kept being charged — a workspace could report "full" with
        room to spare *and* grow without bound. A store without usage
        accounting (e.g. the wiki-page store) reports 0 — duck-typed like the
        CAS pair."""
        measured = await self._measurement(workspace_id, await self._warm(workspace_id))
        if measured is not None:
            return measured.total
        usage = getattr(self._fs, "workspace_usage", None)
        return await usage(workspace_id) if usage is not None else 0

    async def file_size(self, workspace_id: str, path: str) -> int | None:
        """Size of one file (None if absent) — the overwrite credit for a quota
        check. Warm/cold routed, mirroring `workspace_usage` (#538): the credit
        MUST come from the same source as `used`, or the two halves of the
        subtraction disagree and a warm-only file is charged twice."""
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return await sb.size_of(h, path)
        size = getattr(self._fs, "file_size", None)
        return await size(workspace_id, path) if size is not None else None

    async def _measurement(
        self, workspace_id: str, warm: tuple[Sandbox, SandboxHandle] | None
    ) -> _Measurement | None:
        """The workspace's current size, or ``None`` when it is cold (the caller
        falls back to the durable store).

        Normally this is whatever the mirror sweep last installed
        (`record_measurement`) — the sweep traverses every warm sandbox anyway,
        so nobody's request has to. Measuring here is the COLD-START fallback,
        for a pod with no session on this item: one traversal beats answering
        from the durable snapshot, whose additive-only reconciliation is what
        #538 was about.

        `warm` is passed in rather than resolved here so a gated write probes
        sandbox liveness ONCE: the gate and the write that follows it share one
        answer instead of each paying a round-trip."""
        if warm is None:
            self._tree.pop(workspace_id, None)  # went cold; don't serve a stale size
            return None
        cached = self._tree.get(workspace_id)
        if cached is not None and self._now() - cached.at < self._window:
            return cached
        async with self._walk_locks[workspace_id]:
            # Re-check: whoever held the lock has just installed a fresh
            # measurement, and taking it is the point — measuring again would
            # overwrite theirs along with any write recorded against it.
            cached = self._tree.get(workspace_id)
            if cached is not None and self._now() - cached.at < self._window:
                return cached
            now = self._now()
            sb, h = warm
            try:
                total = await sb.disk_usage(h)
            except (SandboxNotFound, SandboxBusy):
                logger.warning(
                    "files: cannot measure workspace %s (sandbox unreachable) — "
                    "falling back to the durable snapshot",
                    workspace_id,
                )
                return None
            measured = _Measurement(now, total)
            self._install(workspace_id, measured)
            return measured

    def measured_usage(self, workspace_id: str) -> int | None:
        """The size the last measurement produced, or None if there isn't one.

        Deliberately does NOT measure: the caller (the mirror sweeper) wants the
        number the walk it just finished produced, and asking for a fresh one
        would trigger the traversal that `record_measurement` exists to avoid."""
        measured = self._tree.get(workspace_id)
        return None if measured is None else measured.total

    def record_measurement(self, workspace_id: str, total: int) -> None:
        """Install a size taken elsewhere — by the mirror sweep, which traverses
        every warm sandbox on its own cadence (#538 follow-up).

        This is what keeps the traversal OFF the request path: without it the
        measurement is taken lazily by whichever request first finds the window
        expired, so that user pays for it and sees its errors."""
        self._install(workspace_id, _Measurement(self._now(), total))

    def forget_measurement(self, workspace_id: str) -> None:
        """Drop the cached size after something changed the workspace *outside*
        this facade, so the next read measures instead of serving a number that
        is already wrong.

        `_adjust` / `_forget` cover writes that come through here, but `exec`
        writes straight into the sandbox and the facade never hears about it.
        On a host-managed-durable deployment nothing else covers for that:
        `record_measurement` is published by `SandboxSync.mirror`, and
        `registry._writeback` returns before the mirror on that branch — so the
        cache is only ever filled by a read that measured inline, and then
        answers from that value for a whole window. Turn end, the one moment
        the FE refetches the usage bar, lands inside it.

        The public twin of `record_measurement`: the sweep installs, the turn
        and terminal boundaries forget. Idempotent — callers fire it on every
        turn, including ones that never measured anything."""
        self._forget(workspace_id)

    def _install(self, workspace_id: str, measured: _Measurement) -> None:
        """Store a measurement, dropping any that have expired.

        The expiry rides along with EVERY install, not just the measuring one:
        once the sweep became the normal source and direct measurement became
        rare, cleanup that only happened on the latter stopped happening."""
        for other, previous in list(self._tree.items()):
            if measured.at - previous.at >= self._window:
                del self._tree[other]
        self._tree[workspace_id] = measured

    def _adjust(self, workspace_id: str, delta: int) -> None:
        """Fold a change this facade just made into the current measurement, so
        a batch stays exact instead of charging a whole window against a
        pre-batch number. A no-op when nothing is measured, or when the size of
        what changed isn't known — the next read measures."""
        measured = self._tree.get(workspace_id)
        if measured is not None:
            measured.total += delta

    async def _publish_usage(self, workspace_id: str, total: int) -> None:
        """Hand a fresh size to whoever is keeping the durable per-person sum.
        Best-effort by design: a ledger write that fails must not fail the user's
        write — the worst it costs is a total that stays stale until the next
        measurement."""
        if self._on_usage is None:
            return
        try:
            await self._on_usage(workspace_id, max(total, 0))
        except Exception:  # noqa: BLE001 — accounting must never break a write
            logger.warning("files: usage publish failed for %s", workspace_id, exc_info=True)

    def _forget(self, workspace_id: str) -> None:
        """Drop the measurement after a change whose size we didn't compute, so
        the next read measures rather than serving a number we know is stale."""
        self._tree.pop(workspace_id, None)

    async def _ensure_headroom(
        self,
        workspace_id: str,
        path: str,
        new_size: int,
        warm: tuple[Sandbox, SandboxHandle] | None,
    ) -> int | None:
        """Refuse a write that would push the workspace past its quota (#538).

        The rule is about GROWTH, not about being over: a write that doesn't make
        the workspace bigger — shrinking a file, replacing it with the same size —
        is always allowed, even when the workspace is already over. Otherwise a
        workspace that went over (the mirror is ungated, so it can) would be
        wedged: the user is told to delete things, but the tools they'd use to
        tidy up are refused too. Deletes are never gated for the same reason.

        Returns the size `path` had before the write — the caller folds
        `new - previous` into the measurement so a batch stays exact. ``None``
        when there is no quota and nothing was looked up; the caller then drops
        its measurement rather than guessing."""
        quota = self._quota_for(workspace_id)
        # The per-person total binds even where the item itself is uncapped, so
        # this cannot short-circuit on `quota` alone — only on there being no
        # rule of either kind to apply.
        if not quota and self._person_gate is None:
            return None
        used, old = await self._usage_and_size(workspace_id, path, warm)
        growth = new_size - old
        if growth > 0:
            if quota and used + growth > quota:
                raise WorkspaceFull(used=used, quota=quota, attempted=new_size)
            if self._person_gate is not None:
                await self._person_gate(workspace_id, used + growth, growth)
        return old

    async def ensure_room_for(self, workspace_id: str, extra_bytes: int) -> None:
        """Refuse up front if `extra_bytes` more would not fit (#538).

        For a caller that grows the workspace across SEVERAL writes — copying a
        directory subtree — checking once before starting is the difference
        between a clean refusal and a half-copied folder the user now has to
        clean up while over quota. Per-write gating alone can only fail in the
        middle.

        Raises the FIRST refusal, which is what a write path wants: it is
        stopping, and one reason is enough. A caller that would rather report
        every reason (the turn gate) asks `room_refusals` instead — and asks it
        NOT to record, because it is not about to write."""
        for refusal in await self.room_refusals(workspace_id, extra_bytes, record=True):
            raise refusal

    async def room_refusals(
        self, workspace_id: str, extra_bytes: int, *, record: bool = True
    ) -> list[Exception]:
        """Every disk rule that `extra_bytes` more would break, in order.

        Nobody is charged for an operation that is not happening. Two things
        decide that: the CALLER saying it is only asking (`record=False`), and
        this method having already collected a refusal — a write path is honest
        about intending to write, right up until another rule stops it.

        The per-person gate is not a pure predicate: on the allowed path it
        writes the post-write size to the ledger, which is only true if that
        write occurs. Collecting every reason means reaching that
        gate even after another rule has already refused the operation, and
        charging there left an owner over-counted for a copy that never ran:
        they were then refused in a DIFFERENT item, against a number that
        appears nowhere in the product — the file tree still showed the smaller
        size. The gate's own comment says a refused write is "deliberately NOT
        recorded"; this is the caller-side half of that rule.

        TWO rules live here — this item's own quota and its owner's total across
        items — and they are independent: being over one says nothing about the
        other. Stopping at the first meant a person out of BOTH was told to
        delete files, deleted them, tried again, and was told to delete files
        somewhere else — the sequence the turn gate exists to prevent, surviving
        here because both refusals come from inside this one method.

        How OFTEN both bind at once is not something this docstring should claim.
        The documented example config puts them 640× apart (`per_app.default.disk`
        80M against `per_user.disk` 50G), so someone would have to fill hundreds
        of items before the personal total binds alongside one item's own. The
        justification is that they are independent rules, not that they fail
        together."""
        refusals: list[Exception] = []
        quota = self._quota_for(workspace_id)
        if extra_bytes <= 0 or (not quota and self._person_gate is None):
            return refusals
        used = await self.workspace_usage(workspace_id)
        if quota and used + extra_bytes > quota:
            refusals.append(WorkspaceFull(used=used, quota=quota, attempted=extra_bytes))
        if self._person_gate is not None:
            try:
                await self._person_gate(
                    workspace_id,
                    used + extra_bytes,
                    extra_bytes,
                    # …and not once ANOTHER rule has already refused. `record`
                    # alone was not enough: it is the CALLER saying "I am only
                    # asking", and a write path legitimately passes True — but a
                    # write path whose workspace rule has just refused is no
                    # longer going to write either. The first version of this
                    # fix set the flag at the two callers and left this line
                    # unconditional, so the folder copy its own commit message
                    # described — refused, yet charged — was untouched.
                    record=record and not refusals,
                )
            except UserDiskFull as exc:
                refusals.append(exc)
        return refusals

    async def _usage_and_size(
        self, workspace_id: str, path: str, warm: tuple[Sandbox, SandboxHandle] | None
    ) -> tuple[int, int]:
        """``(workspace bytes, bytes at path)`` — the quota subtraction's two
        halves, from ONE measurement, so they can never disagree about whether a
        file counts."""
        path = abs_path(path)
        measured = await self._measurement(workspace_id, warm)
        if measured is not None:
            assert warm is not None  # a measurement implies a live sandbox
            sb, h = warm
            return measured.total, (await sb.size_of(h, path) or 0)
        # Cold. Read the durable store DIRECTLY rather than via the public
        # `workspace_usage`/`file_size`, which would each re-resolve liveness —
        # turning one gated write into several sandbox round-trips, and letting
        # the workspace warm up between the two halves so `used` came from the
        # snapshot while `old` came from the sandbox.
        usage = getattr(self._fs, "workspace_usage", None)
        size = getattr(self._fs, "file_size", None)
        used = await usage(workspace_id) if usage is not None else 0
        old = (await size(workspace_id, path) if size is not None else None) or 0
        return used, old

    def quota_of(self, workspace_id: str) -> int:
        """This workspace's byte ceiling (0 ⇒ unlimited) — its App's, resolved.

        Public because the API has to SHOW the number, not just enforce it: the
        507 body and the usage bar both name a limit, and naming a different one
        from the gate would be a worse lie than not showing it at all."""
        return self._quota_for(workspace_id)

    async def remaining_quota(self, workspace_id: str, path: str) -> int | None:
        """Bytes the file at `path` may occupy before the workspace hits its quota
        — the headroom the upload/edit endpoints gate on (#245). An overwrite is
        a *replace*: the existing file's size is credited back, so re-uploading a
        same-size file never falsely rejects. A quota of 0 disables the cap →
        None (no limit). The limit is read from the facade rather than passed in:
        the caller holding its own copy is what let the route's rule drift from
        `_ensure_headroom`'s, and drift here means refusing a shrink on a full
        workspace. Measured against the **live** workspace (#538) — warm ⇒
        the sandbox, cold ⇒ the durable snapshot — so what a user is charged for
        is what the file tree shows them. The mirror still writes the raw store
        directly and stays ungated (#245 choice B: never lose work the agent has
        already done); what changed is that those bytes are now *counted*, so the
        next gated write is the one that gets refused.

        Never less than the file's CURRENT size: the gate is about growth, so a
        path may always keep the bytes it already has. Without that floor this
        arithmetic goes negative as soon as the workspace is over quota — which
        the ungated mirror makes an expected state — and the endpoint would
        refuse even a shrink, wedging the very workspace we are telling the user
        to tidy up. That divergence between this number and `_ensure_headroom`
        is what made the "an over-quota workspace can still be tidied" guarantee
        false on `PUT /files/{path}`, which IS the IDE save and the file-tree
        upload."""
        quota = self._quota_for(workspace_id)
        if not quota:
            return None
        used, old = await self._usage_and_size(workspace_id, path, await self._warm(workspace_id))
        return max(quota - (used - old), old)

    async def delete(self, workspace_id: str, path: str) -> None:
        await self._delete_with(workspace_id, path, await self._warm(workspace_id))

    async def _delete_with(self, workspace_id: str, path: str, warm) -> None:
        """`delete` against an ALREADY-resolved liveness — see `_read_with`."""
        path = abs_path(path)
        # What it cost, so the measurement can be adjusted instead of dropped.
        # Deleting is exactly what a user does when they are out of space, and
        # dropping the measurement would make every one of those deletes force a
        # fresh traversal on the next read. Only worth asking when there IS a
        # measurement to keep — otherwise the next read measures anyway.
        freed: int | None = None
        if warm is not None and self._tree.get(workspace_id) is not None:
            sb, h = warm
            freed = await sb.size_of(h, path)
        if warm is not None:
            sb, h = warm
            try:
                await sb.delete(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.delete(workspace_id, path)
        if freed is None:
            self._forget(workspace_id)
        else:
            self._adjust(workspace_id, -freed)
        # Deleting is exactly what someone at their limit does, so the freed
        # bytes must reach the per-person total NOW — not at the next mirror
        # sweep, by which time they have already retried and been refused again.
        if self._on_usage is not None:
            await self._publish_usage(workspace_id, await self.workspace_usage(workspace_id))

    async def purge(self, workspace_id: str) -> None:
        """FileStore-contract completeness. The item-delete cascade purges the
        DURABLE store directly after tearing the sandbox down (item_routes) —
        it never comes through this facade, because purging a workspace whose
        sandbox is still warm would race the mirror. Delegates to the durable
        store and drops the cached measurement; refuses a warm workspace loudly
        rather than half-deleting under a live sandbox."""
        if await self._warm(workspace_id) is not None:
            raise RuntimeError(
                f"purge({workspace_id!r}) with a warm sandbox — close the session first"
            )
        await self._fs.purge(workspace_id)
        self._forget(workspace_id)
        if self._on_usage is not None:
            await self._publish_usage(workspace_id, 0)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        prefix = abs_path(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [e.path for e in (await sb.walk(h, prefix or "/")).files]
        return await self._fs.ls(workspace_id, prefix)

    async def list_dir(self, workspace_id: str, path: str = "") -> tuple[list[str], list[str]]:
        """ONE level of the tree at `path`: its files, and its immediate
        subdirectories — `ls`, not `find`. Both lists are sorted `/`-rooted
        paths; a subdirectory carries a trailing `/`.

        A recursive listing's size is whatever the workspace happens to hold,
        which is why the agent-facing `list_files` reads a level at a time and
        descends. The split is derived from the recursive walk, since neither
        the Sandbox `walk` nor a FileStore key scan takes a depth — so this
        bounds what the MODEL sees, not yet what the backend scans. Pushing
        depth into both backends is the follow-up.

        `path` is resolved the way a person would mean it, in order:

        - a FILE ⇒ that file alone (answering "does this exist" honestly,
          rather than "no files under /a.txt", which reads as "it's gone");
        - a DIRECTORY ⇒ its level;
        - otherwise a partial NAME ⇒ the entries in its parent that start with
          it. `list_files`'s parameter has always been called `prefix` and used
          to filter by string prefix, so half a name has to keep working — and
          it only ever worked on the cold path anyway (a FileStore key scan is
          a string prefix; `walk` on a non-directory yields nothing), so this
          also makes warm and cold agree for the first time.

        Directories are inferred from the paths of the files under them: an
        empty directory has no files to infer from, so it does not appear —
        the same blind spot `walk` (regular files only) already has."""
        key = _dir_key(path)
        if key and await self.exists(workspace_id, key):
            return [key], []
        files, dirs = _split_level(await self.ls(workspace_id, key), key + "/")
        if files or dirs or not key:
            return files, dirs
        parent = key.rsplit("/", 1)[0]
        siblings = [p for p in await self.ls(workspace_id, parent) if p.startswith(key)]
        return _split_level(siblings, parent + "/")

    async def stat_all(self, workspace_id: str, prefix: str = "") -> list[tuple[str, int]]:
        """Every file under ``prefix`` as ``(path, size)`` — WITHOUT reading a
        single file's bytes (#362). The file-tree endpoint only needs each
        file's size, and both routes already carry it as cheap metadata:

        - **warm**: ``walk`` returns ``FileEntry(path, size)`` (a stat, never a
          read), so a 600-file tree costs one directory traversal, not 600
          full-content downloads.
        - **cold**: the durable store exposes a batch ``stat_all`` (duck-typed,
          like ``file_size`` / ``workspace_usage``) that reads each record's
          inline ``size`` metadata, never restoring the offloaded blob.

        A store without that optimisation (an exotic backend) degrades to paths
        with an unknown size of 0 — still blob-free."""
        prefix = abs_path(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [(e.path, e.size) for e in (await sb.walk(h, prefix or "/")).files]
        return await self._stat_all_cold(workspace_id, prefix)

    async def _stat_all_cold(self, workspace_id: str, prefix: str) -> list[tuple[str, int]]:
        batch = getattr(self._fs, "stat_all", None)
        if batch is not None:
            return await batch(workspace_id, prefix)
        return [(p, 0) for p in await self._fs.ls(workspace_id, prefix)]

    async def mkdir(self, workspace_id: str, path: str) -> None:
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.mkdir(h, path)
        else:
            await self._fs.mkdir(workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.rmdir(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.rmdir(workspace_id, path)
        # A subtree went away — too many paths to patch one by one, so drop the
        # measurement and let the next read re-walk.
        self._tree.pop(workspace_id, None)

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            base = path.rstrip("/") + "/"
            walked = await sb.walk(h, "/")
            return path in walked.dirs or any(e.path.startswith(base) for e in walked.files)
        return await self._fs.is_dir(workspace_id, path)

    async def tree(
        self, workspace_id: str, prefix: str = ""
    ) -> tuple[list[tuple[str, int]], list[str]]:
        """Files (with sizes) and directories from ONE traversal.

        `stat_all` and `listdir` each walked the whole workspace, and the file
        tree needs both, so drawing it stat-ed every file twice to answer two
        halves of one question. Warm, that traversal crosses the network behind a
        liveness probe; cold it is a durable listing. Either way, asking once and
        splitting the result costs nothing extra.

        Directories come back from the traversal itself, not derived from the
        file paths: an EMPTY directory appears in no file path, so deriving them
        silently dropped every folder that held no files — the folder a user had
        just created was never drawn. Only the durable branch still derives, and
        it unions that with the store's own dir record."""
        prefix = abs_path(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            walked = await sb.walk(h, prefix or "/")
            return [(e.path, e.size) for e in walked.files], sorted(walked.dirs)
        files = await self._stat_all_cold(workspace_id, prefix)
        stored = await self._fs.listdir(workspace_id, prefix)
        return files, sorted(set(stored) | set(_dirs_of(p for p, _ in files)))

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        prefix = abs_path(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return sorted((await sb.walk(h, prefix or "/")).dirs)
        return await self._fs.listdir(workspace_id, prefix)

    # ---- compare-and-swap writes (the agent must declare its expectation) ----

    async def create(self, workspace_id: str, path: str, data: bytes) -> bytes | None:
        """Create-only write: succeed (return None) if `path` doesn't exist;
        otherwise don't clobber — return the current bytes so the caller can
        decide. Atomic under the per-path lock."""
        path = abs_path(path)
        async with self._locks[(workspace_id, path)]:
            if await self.exists(workspace_id, path):
                return await self.read(workspace_id, path)
            await self.write(workspace_id, path, data)
            return None

    async def edit(self, workspace_id: str, path: str, old: str, new: str) -> str | None:
        """Replace the **unique** occurrence of `old` with `new`. Succeed
        (return None) only when `old` appears exactly once; otherwise it's a
        conflict (missing file, text not found, or ambiguous) and the current
        text is returned so the caller can re-base. Atomic under the per-path
        lock — so a concurrent change makes `old` stop matching and the edit is
        rejected rather than blindly applied.

        When the file store exposes optimistic-concurrency hooks
        (``read_with_etag`` + ``write_cas``) and no live sandbox owns the
        workspace, the read→write is additionally guarded by the store's etag,
        so the edit is safe against writers in *other processes* (e.g. a second
        ingest worker), not just other coroutines — the per-path lock only
        covers this process."""
        path = abs_path(path)
        warm = await self._warm(workspace_id)
        write_cas = getattr(self._fs, "write_cas", None)
        read_with_etag = getattr(self._fs, "read_with_etag", None)
        async with self._locks[(workspace_id, path)]:
            if warm is None and write_cas is not None and read_with_etag is not None:
                return await self._edit_cas(workspace_id, path, old, new, write_cas, read_with_etag)
            try:
                current = (await self.read(workspace_id, path)).decode("utf-8", errors="replace")
            except FileNotFound:
                return ""
            if current.count(old) != 1:
                return current
            await self.write(workspace_id, path, current.replace(old, new, 1).encode("utf-8"))
            return None

    async def _edit_cas(
        self,
        workspace_id: str,
        path: str,
        old: str,
        new: str,
        write_cas: Callable[[str, str, bytes, str | None], Awaitable[bool]],
        read_with_etag: Callable[[str, str], Awaitable[tuple[bytes, str] | None]],
    ) -> str | None:
        """Etag-guarded edit→retry: re-read on every attempt so a concurrent
        write makes us re-base off the latest content instead of clobbering it."""
        for _ in range(_CAS_EDIT_RETRIES):
            got = await read_with_etag(workspace_id, path)
            if got is None:
                return ""  # the page doesn't exist — re-create it with write_file
            data, etag = got
            current = data.decode("utf-8", errors="replace")
            if current.count(old) != 1:
                return current  # text conflict — caller re-reads and re-bases
            updated = current.replace(old, new, 1).encode("utf-8")
            # This branch reaches the store directly rather than through `write`,
            # so it needs the quota check of its own — otherwise "every write is
            # gated" would quietly stop being true for whichever store grows a
            # `write_cas`. Today only the (unquota'd) wiki store has one.
            await self._ensure_headroom(workspace_id, path, len(updated), None)
            applied = await write_cas(workspace_id, path, updated, etag)
            if applied:
                return None
            # A concurrent writer bumped the etag between our read and write —
            # loop to re-read and re-apply against the new content.
        # Persistent contention: hand back the latest content as a conflict.
        got = await read_with_etag(workspace_id, path)
        return got[0].decode("utf-8", errors="replace") if got is not None else ""


async def read_all(store: FileStore, workspace_id: str, paths: Sequence[str]) -> list[bytes]:
    """Read `paths` as ONE operation where the store can (`read_many` — the
    WorkspaceFiles facade), else one at a time. Order matches `paths`.

    THE place this rule is spelled. Reading a set of files a call at a time
    re-resolves the workspace's liveness per file, which against the hosted
    sandbox is a second network round trip in front of every one of them — the
    defect behind the entity/workflow/skill listings. Duck-typed like the
    store's other optional capabilities (`stat_all`, the CAS pair), so the wiki
    store and the test doubles need not grow a method.

    Every caller that reads a batch of paths goes through here rather than
    duck-typing `read_many` itself: two spellings of one rule drift, and the
    one that drifts is the one nobody measured."""
    read_many = getattr(store, "read_many", None)
    if read_many is not None:
        return list(await read_many(workspace_id, paths))
    return [await store.read(workspace_id, path) for path in paths]


async def read_all_existing(
    store: FileStore, workspace_id: str, paths: Sequence[str]
) -> dict[str, bytes]:
    """`read_all`, but a path that is gone is OMITTED rather than an error.

    For a listing: `ls` names the files and the reads happen after, so a file
    deleted in between is a race, not a corrupt workspace — the rest of the list
    still has to render. Reading one at a time made that tolerance free (the
    caller's loop skipped a `FileNotFound` and carried on); batching is what puts
    a whole listing at the mercy of one vanished file, so the tolerance has to
    be stated here instead of inherited.

    The batch is tried first and only a real miss pays for the per-path retry,
    so the ordinary case keeps the single resolution."""
    try:
        return dict(zip(paths, await read_all(store, workspace_id, paths), strict=True))
    except (FileNotFound, FileNotFoundError):
        got: dict[str, bytes] = {}
        for path in paths:
            try:
                got[path] = await store.read(workspace_id, path)
            except (FileNotFound, FileNotFoundError):
                continue
        return got
