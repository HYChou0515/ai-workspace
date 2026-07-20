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
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..filestore.protocol import FileExists, FileNotFound, FileStore
from ..sandbox.protocol import Sandbox, SandboxBusy, SandboxHandle, SandboxNotFound
from ..sync.ignore import DEFAULT_IGNORES, should_ignore

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
    """A workspace's file sizes and their total, kept in step.

    The total is maintained rather than re-summed because the quota is asked on
    every gated write; and it lives HERE, next to the map it summarises, so the
    two cannot drift — the reason not to keep a running total was the risk of
    maintaining it in several places, which this removes rather than accepts.

    The map is not an optimisation: the quota needs both the workspace's total
    AND the size of the file being overwritten, and taking those from different
    sources is what once made a warm-only file count against the workspace but
    credit back as zero."""

    __slots__ = ("at", "sizes", "total")

    def __init__(self, at: float, sizes: dict[str, int]) -> None:
        self.at = at
        self.sizes = sizes
        self.total = sum(sizes.values())

    def set(self, path: str, size: int) -> None:
        self.total += size - self.sizes.get(path, 0)
        self.sizes[path] = size

    def drop(self, path: str) -> None:
        self.total -= self.sizes.pop(path, 0)


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


def _norm(path: str) -> str:
    """Canonicalise a workspace path: ``./brief.md``, ``brief.md`` and
    ``/brief.md`` all map to the same internal key ``/brief.md``. So
    the agent can write whichever feels natural in prose and the
    underlying store stays consistent."""
    p = path.removeprefix("./")
    return p if p.startswith("/") else "/" + p


def rel_path(path: str) -> str:
    """`_norm`'s inverse — the workspace path as an AGENT should ever see it.

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
    `_norm` — the agent is told the three spellings are interchangeable, so a
    bare `.` must not resolve to a directory literally named `.`."""
    p = path.strip()
    if p in ("", ".", "./", "/"):
        return ""
    return _norm(p).rstrip("/")


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


class WorkspaceFiles:
    def __init__(
        self,
        filestore: FileStore,
        sandbox: Sandbox | None = None,
        handle_for: Callable[[str], Awaitable[SandboxHandle | None]] | None = None,
        rebuild: Callable[[str], Awaitable[SandboxHandle]] | None = None,
        quota: int = 0,
        ignores: list[str] | None = None,
        usage_window: float = _USAGE_WINDOW_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fs = filestore
        self._sb = sandbox
        # #538: bytes one workspace may occupy; 0 ⇒ unlimited (the default, so the
        # wiki-page stores and other non-workspace uses are never gated).
        self._quota = quota
        # #538: the paths the measurement skips — the SAME set `SandboxSync.mirror`
        # filters out before writing the durable store. The quota protects that
        # durable disk, so charging for bytes the mirror deliberately never sends
        # there would let one `npm install` inside the workspace eat the whole
        # quota with content that is never persisted, and would make the number
        # jump the moment a reap moved the measurement to the durable side.
        # (`registry._scratch_usage` counting them is correct — its cap guards the
        # scratch volume, which really does hold them.)
        self._ignores = DEFAULT_IGNORES if ignores is None else ignores
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
        try:
            await self._sb.exists(handle, "/")  # SandboxNotFound = gone; SandboxBusy propagates
        except SandboxNotFound:
            if self._rebuild is None:
                return None  # local shared-vol cold dir → durable snapshot (#345)
            handle = await self._rebuild(workspace_id)  # http: reaped but warm → rebuild
        return (self._sb, handle)

    async def read(self, workspace_id: str, path: str) -> bytes:
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                return await sb.download(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        return await self._fs.read(workspace_id, path)

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        path = _norm(path)
        warm = await self._warm(workspace_id)
        await self._ensure_headroom(workspace_id, path, len(data), warm)
        await self._write_unchecked(workspace_id, path, data, warm)

    async def _write_unchecked(
        self,
        workspace_id: str,
        path: str,
        data: bytes,
        warm: tuple[Sandbox, SandboxHandle] | None,
    ) -> None:
        """The write itself, without the quota gate — for callers that have
        already established the operation cannot grow the workspace."""
        if warm is not None:
            sb, h = warm
            await sb.upload(h, data, path)
        else:
            await self._fs.write(workspace_id, path, data)
        self._record(workspace_id, path, len(data))

    async def move(self, workspace_id: str, src: str, dst: str) -> None:
        """Relocate one file. **Not** quota-gated, and deliberately so: the bytes
        land under a new name and leave the old one, so the workspace's size is
        unchanged (#538).

        Gating it per-write would mean a rename needs headroom for a second copy
        of the file — so renaming anything in a workspace that is more than half
        full would be refused, and renaming a folder would need room for the
        whole tree. Worse, the rename a user reaches for to tidy up is exactly
        the operation an over-quota workspace must not refuse. The source is
        removed immediately after the destination lands, so a failure can only
        leave a harmless duplicate, never a hole."""
        src, dst = _norm(src), _norm(dst)
        data = await self.read(workspace_id, src)
        await self._write_unchecked(workspace_id, dst, data, await self._warm(workspace_id))
        await self.delete(workspace_id, src)

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
        path = _norm(path)
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
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            if await sb.exists(h, path):
                raise FileExists(path)
            await self._ensure_headroom(workspace_id, path, len(data), warm)
            await sb.upload(h, data, path)
            self._record(workspace_id, path, len(data))
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
        path = _norm(path)
        # The streaming upload route also checks mid-stream so an over-quota body
        # is rejected before it's staged; this is the backstop that keeps the rule
        # true for any future caller that doesn't.
        warm = await self._warm(workspace_id)
        await self._ensure_headroom(workspace_id, path, source.stat().st_size, warm)
        if warm is not None:
            sb, h = warm
            await sb.upload_file(h, source, path)
        else:
            await self._fs.write_from_path(workspace_id, path, source, content_type)
        self._record(workspace_id, path, source.stat().st_size)

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        """Like `read`, but stream the bytes out to the on-disk `dest` — RAM-free
        for big files (issue #219). Routes warm→sandbox / cold→snapshot like
        `read`; a missing file maps to `FileNotFound`."""
        path = _norm(path)
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
        path = _norm(path)
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
        path = _norm(path)
        measured = await self._measurement(workspace_id, await self._warm(workspace_id))
        if measured is not None:
            return measured.sizes.get(path)
        size = getattr(self._fs, "file_size", None)
        return await size(workspace_id, path) if size is not None else None

    async def _measurement(
        self, workspace_id: str, warm: tuple[Sandbox, SandboxHandle] | None
    ) -> _Measurement | None:
        """The workspace's current measurement, or ``None`` when it is cold (the
        caller falls back to the durable store).

        Normally this is whatever the mirror sweep last installed
        (`record_measurement`) — the sweep walks every warm sandbox anyway, so
        nobody's request has to. Walking here is the COLD-START fallback, for a
        pod that has no session on this item and therefore never swept it: it is
        better to pay one traversal than to answer from the durable snapshot,
        whose additive-only reconciliation is what #538 was about.

        `warm` is passed in rather than resolved here so a gated write probes
        sandbox liveness ONCE: the gate and the write that follows it share one
        answer instead of each paying a round-trip."""
        if warm is None:
            self._tree.pop(workspace_id, None)  # went cold; don't serve stale sizes
            return None
        cached = self._tree.get(workspace_id)
        if cached is not None and self._now() - cached.at < self._window:
            return cached
        async with self._walk_locks[workspace_id]:
            # Re-check: whoever held the lock has just installed a fresh
            # measurement, and taking it is the point — a second walk would
            # overwrite theirs along with any write recorded against it.
            cached = self._tree.get(workspace_id)
            if cached is not None and self._now() - cached.at < self._window:
                return cached
            return await self._walk_into_memo(workspace_id, warm)

    async def _walk_into_memo(
        self, workspace_id: str, warm: tuple[Sandbox, SandboxHandle]
    ) -> _Measurement | None:
        """Measure by walking. Returns ``None`` when the sandbox cannot be read —
        the caller then answers from the durable store rather than failing, so a
        busy or just-reaped sandbox degrades the number's freshness instead of
        turning a usage read into a 500 or refusing a write outright."""
        now = self._now()
        sb, h = warm
        try:
            entries = await sb.walk(h, "/")
        except (SandboxNotFound, SandboxBusy):
            logger.warning(
                "files: cannot measure workspace %s (sandbox unreachable) — "
                "falling back to the durable snapshot",
                workspace_id,
            )
            return None
        sizes = {e.path: e.size for e in entries if not should_ignore(e.path, self._ignores)}
        measured = _Measurement(now, sizes)
        self._install(workspace_id, measured)
        return measured

    def record_measurement(self, workspace_id: str, sizes: dict[str, int]) -> None:
        """Install a measurement taken elsewhere — by the mirror sweep, which
        already walks every warm sandbox on its own cadence (#538 follow-up).

        This is what keeps the walk OFF the request path: without it the
        measurement is taken lazily by whichever request first finds the window
        expired, so that user pays for the traversal and sees its errors. The
        sweep filters the same ignore set it mirrors with, so the quota counts
        exactly the bytes that reach the durable store."""
        self._install(workspace_id, _Measurement(self._now(), sizes))

    def _install(self, workspace_id: str, measured: _Measurement) -> None:
        """Store a measurement, dropping any that have expired.

        The expiry rides along with EVERY install, not just the walking one: a
        pod serves many items over its life and each measurement is the size of
        a file tree, so once the sweep became the normal source and walks became
        rare, cleanup that only happened on a walk stopped happening at all."""
        for other, previous in list(self._tree.items()):
            if measured.at - previous.at >= self._window:
                del self._tree[other]
        self._tree[workspace_id] = measured

    def _record(self, workspace_id: str, path: str, size: int | None) -> None:
        """Fold a write (``size``) or a delete (``None``) this facade just made
        into the current measurement, so a batch of writes stays exact instead of
        charging against a pre-batch number for a whole window. A no-op when
        nothing is measured — the next read measures."""
        measured = self._tree.get(workspace_id)
        if measured is None:
            return
        if size is None:
            measured.drop(path)
        else:
            measured.set(path, size)

    async def _ensure_headroom(
        self,
        workspace_id: str,
        path: str,
        new_size: int,
        warm: tuple[Sandbox, SandboxHandle] | None,
    ) -> None:
        """Refuse a write that would push the workspace past its quota (#538).

        The rule is about GROWTH, not about being over: a write that doesn't make
        the workspace bigger — shrinking a file, replacing it with the same size —
        is always allowed, even when the workspace is already over. Otherwise a
        workspace that went over (the mirror is ungated, so it can) would be
        wedged: the user is told to delete things, but the tools they'd use to
        tidy up are refused too. Deletes are never gated for the same reason."""
        if not self._quota:
            return
        used, old = await self._usage_and_size(workspace_id, path, warm)
        growth = new_size - old
        if growth > 0 and used + growth > self._quota:
            raise WorkspaceFull(used=used, quota=self._quota, attempted=new_size)

    async def ensure_room_for(self, workspace_id: str, extra_bytes: int) -> None:
        """Refuse up front if `extra_bytes` more would not fit (#538).

        For a caller that grows the workspace across SEVERAL writes — copying a
        directory subtree — checking once before starting is the difference
        between a clean refusal and a half-copied folder the user now has to
        clean up while over quota. Per-write gating alone can only fail in the
        middle."""
        if not self._quota or extra_bytes <= 0:
            return
        used = await self.workspace_usage(workspace_id)
        if used + extra_bytes > self._quota:
            raise WorkspaceFull(used=used, quota=self._quota, attempted=extra_bytes)

    async def _usage_and_size(
        self, workspace_id: str, path: str, warm: tuple[Sandbox, SandboxHandle] | None
    ) -> tuple[int, int]:
        """``(workspace bytes, bytes at path)`` — the quota subtraction's two
        halves, from ONE measurement, so they can never disagree about whether a
        file counts."""
        path = _norm(path)
        measured = await self._measurement(workspace_id, warm)
        if measured is not None:
            return measured.total, measured.sizes.get(path, 0)
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

    async def remaining_quota(self, workspace_id: str, path: str, quota: int) -> int | None:
        """Bytes the file at `path` may occupy before the workspace hits `quota`
        — the headroom the upload/edit endpoints gate on (#245). An overwrite is
        a *replace*: the existing file's size is credited back, so re-uploading a
        same-size file never falsely rejects. `quota` of 0 disables the cap →
        None (no limit). Measured against the **live** workspace (#538) — warm ⇒
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
        if not quota:
            return None
        used, old = await self._usage_and_size(workspace_id, path, await self._warm(workspace_id))
        return max(quota - (used - old), old)

    async def delete(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            try:
                await sb.delete(h, path)
            except FileNotFoundError as exc:
                raise FileNotFound(path) from exc
        else:
            await self._fs.delete(workspace_id, path)
        self._record(workspace_id, path, None)

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        prefix = _norm(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [e.path for e in await sb.walk(h, prefix or "/")]
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
        prefix = _norm(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            return [(e.path, e.size) for e in await sb.walk(h, prefix or "/")]
        batch = getattr(self._fs, "stat_all", None)
        if batch is not None:
            return await batch(workspace_id, prefix)
        return [(p, 0) for p in await self._fs.ls(workspace_id, prefix)]

    async def mkdir(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            await sb.mkdir(h, path)
        else:
            await self._fs.mkdir(workspace_id, path)

    async def rmdir(self, workspace_id: str, path: str) -> None:
        path = _norm(path)
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
        path = _norm(path)
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            base = path.rstrip("/") + "/"
            return any(e.path.startswith(base) for e in await sb.walk(h, "/"))
        return await self._fs.is_dir(workspace_id, path)

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        prefix = _norm(prefix) if prefix else prefix
        warm = await self._warm(workspace_id)
        if warm is not None:
            sb, h = warm
            dirs: set[str] = set()
            for e in await sb.walk(h, prefix or "/"):
                parts = e.path.strip("/").split("/")
                for i in range(1, len(parts)):
                    dirs.add("/" + "/".join(parts[:i]))
            return sorted(dirs)
        return await self._fs.listdir(workspace_id, prefix)

    # ---- compare-and-swap writes (the agent must declare its expectation) ----

    async def create(self, workspace_id: str, path: str, data: bytes) -> bytes | None:
        """Create-only write: succeed (return None) if `path` doesn't exist;
        otherwise don't clobber — return the current bytes so the caller can
        decide. Atomic under the per-path lock."""
        path = _norm(path)
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
        path = _norm(path)
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
