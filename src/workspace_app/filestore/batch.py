"""Workspace-path spelling and batched reads over any `FileStore`.

Moved out of `files.facade` (which pulls in the sandbox, quota and specstar
layers) so that code which only needs a `FileStore` — the entity catalog and
store — can run without them: a view plugin's sandbox half vendors the entity
reader (#847/#848, `entity/local.py`). `files.facade` re-exports all four, so
its importers are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from .protocol import FileNotFound, FileStore


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


async def read_all(store: FileStore, workspace_id: str, paths: Sequence[str]) -> list[bytes]:
    """Read `paths` as ONE operation where the store can (`read_many_existing`
    — the WorkspaceFiles facade has it, so does the durable store), else one at
    a time. Order matches `paths`; a path with no file raises `FileNotFound`.

    THE place this rule is spelled. Reading a set of files a call at a time
    re-resolves the workspace's liveness per file, which against the hosted
    sandbox is a second network round trip in front of every one of them — the
    defect behind the entity/workflow/skill listings. Duck-typed like the
    store's other optional capabilities (`stat_all`, the CAS pair), so the wiki
    store and the test doubles need not grow a method.

    Every caller that reads a batch of paths goes through here rather than
    duck-typing the capability itself: two spellings of one rule drift, and the
    one that drifts is the one nobody measured."""
    batch = getattr(store, "read_many_existing", None)
    if batch is None:
        return [await store.read(workspace_id, path) for path in paths]
    # The WHOLE ask goes down in one call, deliberately. Chunking here called
    # the store once per chunk, and through the facade that resolved the
    # workspace's liveness once per chunk — a sandbox reaped mid-read would then
    # have had some chunks answered by it and the rest by the durable snapshot.
    # A store that batches BOUNDS ITS OWN REQUEST (see `FileStore`).
    found = await batch(workspace_id, paths)
    out: list[bytes] = []
    for path in paths:
        # Look up BOTH spellings. The facade keys its answer by `abs_path`, a
        # raw store keys it by the path as asked, and `abs_path` is identity on
        # an already-canonical one — so this is the only lookup that works for
        # both without either side having to know which it is talking to.
        #
        # This used to be positional (zip the blobs back against the order asked)
        # and normalisation could not matter. Turning it into a dict lookup made
        # `read_all(files, ws, ["a.md"])` raise for a file that is plainly there:
        # a wrong answer over intact data, on the entry point this module tells
        # every caller to route through. `is None`, never falsy — an EMPTY file
        # is a file.
        blob = found.get(path)
        if blob is None:
            blob = found.get(abs_path(path))
        if blob is None:
            raise FileNotFound(path)
        out.append(blob)
    return out


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

    A store that can answer for a whole set at once is asked for the LENIENT
    form (`read_many_existing`), so the miss costs the miss. Letting the strict
    batch raise and then re-reading every path singly was worse than the
    per-file loop it replaced: measured, one vanished file among ten turned one
    batch into a second full fetch."""
    batch = getattr(store, "read_many_existing", None)
    if batch is not None:
        return dict(await batch(workspace_id, paths))
    got: dict[str, bytes] = {}
    for path in paths:
        try:
            got[path] = await store.read(workspace_id, path)
        except (FileNotFound, FileNotFoundError):
            continue
    return got
