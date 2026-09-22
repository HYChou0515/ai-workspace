"""Which durable stores this deployment actually writes to.

Invariant one of `docs/plan-backup.md`: **the set of stores a backup covers equals
the set the app writes to, and when they cannot be shown equal the backup fails
rather than covering less.**

The only way to hold that is to read the same `Settings` the app boots from and
answer with the same branches `factories` takes, rather than keeping a path list
in a CronJob manifest. `factories.get_filestore` and
`factories.get_sandbox_filestore` are the authority for what a legal deployment
looks like; `tests/backup/test_sources_exhaustive.py` reads their `match` arms out
of the source and fails when one appears here without a home.

The aliasing trap this exists for: `sandbox.durable.kind: ""` does **not** mean
"no sandbox data". It means the sandbox writes into the API filestore — the same
store `filestore.kind` names. A hand-written list gets that backwards and either
backs one tree up twice or misses both.

Deliberately not a backup target:

* `sandbox.root` (the scratch volume) — disposable by construction; the idle
  reaper recycles a workspace into the durable store before freeing it.
* `message_queue` — with `kind: simple` a job IS a specstar resource, so it is
  already inside the specstar source; with `rabbitmq` it is in the broker, and
  queued work is not data worth restoring.
* `observability.llm_log` — a call log, not user data. It does grow without a
  bound (`keep_days: 0` keeps everything), which is a separate operational
  concern, not a backup one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..config.schema import Settings

SourceKind = Literal["specstar", "tree"]


class UnsupportedDeployment(Exception):
    """A config this backup cannot prove it covers.

    Raised — never swallowed, never downgraded to "skip that one" — when a store
    kind has no handler here. A deployment that boots but cannot be backed up has
    to say so loudly at backup time; the alternative is a backup that silently
    holds less than it claims, which is discovered on the day of the restore.
    Same shape as `worker.select_coordinator`: fail loud rather than idle silently.
    """


@dataclass(frozen=True)
class DurableSource:
    """One store to back up.

    ``root`` is a filesystem path when the store has one — the backup checks it is
    a mount point before trusting what it reads there, which is what catches an
    NFS volume that failed to mount and would otherwise be archived as an empty
    tree. It is ``""`` for a store with no local path (a Postgres-backed specstar).

    ``why`` names the config that put this source in the list, verbatim enough to
    grep for. It ends up in the backup receipt so an operator reading one can tell
    coverage from a guess.
    """

    name: str
    kind: SourceKind
    root: str
    why: str


def durable_sources(settings: Settings) -> tuple[DurableSource, ...]:
    """The durable stores this `settings` makes the app write to.

    Order matters for the caller: the specstar store comes first because it is the
    small, high-value half — the one a bad release needs rolled back — and the
    workspace tree follows.
    """
    sources: list[DurableSource] = []
    specstar = _specstar_source(settings)
    if specstar is not None:
        sources.append(specstar)
    tree = _workspace_tree_source(settings)
    if tree is not None:
        sources.append(tree)
    return tuple(sources)


def _specstar_source(settings: Settings) -> DurableSource | None:
    """The API's own store. Mirrors `factories.get_filestore`.

    ``memory`` is a legal kind that persists nothing, so it contributes no source
    — but that is a real answer, not a skipped one: the caller reports a
    deployment with no durable store instead of writing an empty archive.
    """
    fs = settings.filestore
    match fs.kind:
        case "memory":
            return None
        case "specstar":
            if not fs.disk_root and not fs.pg_dsn:
                # `factories._backend_for` returns None here and specstar falls
                # back to its in-memory default — the store boots and persists
                # nothing. Dev-only, and nothing to archive.
                return None
            return DurableSource(
                name="specstar",
                kind="specstar",
                root=fs.disk_root,
                why="filestore.kind: specstar",
            )
        case other:
            raise UnsupportedDeployment(
                f"filestore.kind: {other!r} has no backup handler. "
                "`factories.get_filestore` accepts it, so this deployment boots and "
                "writes somewhere this backup does not know how to read."
            )


def _workspace_tree_source(settings: Settings) -> DurableSource | None:
    """The sandbox's durable workspace store. Mirrors `factories.get_sandbox_filestore`.

    ``""`` returns None on purpose and it is the subtle case: the sandbox is
    writing into the API filestore, which `_specstar_source` already covers.
    Returning a source here would archive the same store twice.
    """
    d = settings.sandbox.durable
    match d.kind:
        case "":
            return None
        case "nfs_tree":
            if not d.nfs_root:
                raise UnsupportedDeployment(
                    "sandbox.durable.kind: nfs_tree without sandbox.durable.nfs_root — "
                    "`factories.get_sandbox_filestore` refuses this too, so the app "
                    "would not have booted."
                )
            # `migrate_from: specstar` puts workspace data in BOTH stores (read the
            # tree, fall back to specstar, lazily backfill). Coverage still holds
            # without a third source: the specstar half is already source one. It
            # is worth naming in `why` so a restore knows both halves carry
            # workspace files.
            why = "sandbox.durable.kind: nfs_tree"
            match d.migrate_from:
                case "":
                    pass
                case "specstar":
                    why += " (+ migrate_from: specstar — the specstar store also "
                    why += "holds workspace files not yet backfilled)"
                case other:
                    raise UnsupportedDeployment(
                        f"sandbox.durable.migrate_from: {other!r} has no backup handler."
                    )
            return DurableSource(
                name="sandbox-workspaces",
                kind="tree",
                root=d.nfs_root,
                why=why,
            )
        case other:
            raise UnsupportedDeployment(
                f"sandbox.durable.kind: {other!r} has no backup handler. "
                "`factories.get_sandbox_filestore` accepts it, so this deployment "
                "boots and writes somewhere this backup does not know how to read."
            )
