"""The guard behind invariant one: **every deployment shape that boots has a backup
handler, or CI is red.**

The failure this prevents is the one `docs/plan-backup.md` was written around — a
new storage backend lands in `factories`, the app boots on it, and the backup
quietly covers less than it claims. Nobody finds out until a restore.

The legal values are read out of `factories.py` itself, because that is what
decides whether a deployment boots. Deriving them from `backup/sources.py` — the
thing under test — would make this guard agree with any bug it contains. If
someone adds `case "s3_tree"` to `get_sandbox_filestore` and not to
`durable_sources`, this test fails on the new arm.

It has its own positive control: the extractor must find the arms we know are
there, so a refactor from `match` to `if/elif` breaks the test loudly instead of
quietly reducing it to "zero arms, all handled".
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import workspace_app.factories as factories_module
from workspace_app.backup import UnsupportedDeployment, durable_sources
from workspace_app.config.schema import (
    FilestoreSettings,
    MessageQueueSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)

_FACTORIES = pathlib.Path(factories_module.__file__)


def _queue_kinds() -> frozenset[str]:
    """Legal `message_queue.kind` values, read off the factory that builds one.

    `get_job_queue_factory` is an if/elif chain rather than a `match`, so the
    literals are extracted from its comparisons. The axis matters even though
    `durable_sources` returns no source for it: `kind: simple` stores a job as a
    specstar resource, which is WHY the queue needs no source of its own. A third
    kind that persisted somewhere else would break that reasoning silently.
    """
    tree = ast.parse(_FACTORIES.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or "queue" not in node.name:
            continue
        found = {
            c.value
            for cmp in ast.walk(node)
            if isinstance(cmp, ast.Compare) and "kind" in ast.unparse(cmp.left)
            for c in cmp.comparators
            if isinstance(c, ast.Constant) and isinstance(c.value, str)
        }
        if found:
            return frozenset(found)
    raise AssertionError(
        "no message_queue kind comparison found in factories — the extractor is "
        "reading the wrong shape, so this axis proves nothing."
    )


def _match_arms(func_name: str, subject: str) -> frozenset[str]:
    """Every string literal `case` in the `match` on *subject* inside *func_name*.

    The catch-all (`case other:`) is a `MatchAs`, not a `MatchValue`, so it drops
    out on its own — which is right: it is the arm that raises.
    """
    tree = ast.parse(_FACTORIES.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != func_name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Match) or ast.unparse(inner.subject) != subject:
                continue
            return frozenset(
                case.pattern.value.value
                for case in inner.cases
                if isinstance(case.pattern, ast.MatchValue)
                and isinstance(case.pattern.value, ast.Constant)
                and isinstance(case.pattern.value.value, str)
            )
    raise AssertionError(
        f"no `match {subject}` found in {func_name} — the extractor is reading the "
        "wrong shape, so this guard proves nothing. Fix the extractor before "
        "trusting a green run."
    )


FILESTORE_KINDS = _match_arms("get_filestore", "settings.filestore.kind")
DURABLE_KINDS = _match_arms("get_sandbox_filestore", "d.kind")
MIGRATE_FROMS = _match_arms("get_sandbox_filestore", "d.migrate_from")
QUEUE_KINDS = _queue_kinds()


def test_the_extractor_still_reads_the_arms_it_is_supposed_to():
    """Positive control for the guard itself. Without this a `match` → `if/elif`
    refactor would leave every parametrised case below with nothing to iterate,
    and the suite would go green on zero coverage."""
    assert "specstar" in FILESTORE_KINDS
    assert "nfs_tree" in DURABLE_KINDS
    assert "" in DURABLE_KINDS  # the "follow the API filestore" alias
    assert "specstar" in MIGRATE_FROMS
    assert "rabbitmq" in QUEUE_KINDS


@pytest.mark.parametrize("kind", sorted(FILESTORE_KINDS))
def test_every_bootable_filestore_kind_has_a_backup_handler(kind: str):
    settings = Settings(filestore=FilestoreSettings(kind=kind, disk_root="/data"))
    durable_sources(settings)  # must not raise


@pytest.mark.parametrize("kind", sorted(DURABLE_KINDS))
def test_every_bootable_durable_kind_has_a_backup_handler(kind: str):
    settings = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        sandbox=SandboxSettings(
            durable=SandboxDurableSettings(kind=kind, nfs_root="/mnt/workspaces")
        ),
    )
    durable_sources(settings)  # must not raise


@pytest.mark.parametrize("migrate_from", sorted(MIGRATE_FROMS))
def test_every_bootable_migrate_from_has_a_backup_handler(migrate_from: str):
    settings = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        sandbox=SandboxSettings(
            durable=SandboxDurableSettings(
                kind="nfs_tree", nfs_root="/mnt/workspaces", migrate_from=migrate_from
            )
        ),
    )
    durable_sources(settings)  # must not raise


def test_a_kind_factories_does_not_accept_is_refused_here_too():
    """The discriminating half. Without it the three tests above would also pass on
    a `durable_sources` that never raises at all."""
    unknown = "definitely-not-an-arm"
    assert unknown not in FILESTORE_KINDS

    with pytest.raises(UnsupportedDeployment):
        durable_sources(Settings(filestore=FilestoreSettings(kind=unknown, disk_root="/data")))


@pytest.mark.parametrize("kind", sorted(QUEUE_KINDS))
def test_every_bootable_queue_kind_still_needs_no_source_of_its_own(kind: str):
    """The queue is excluded from the backup for a REASON, and the reason is
    conditional on which kinds exist.

    `kind: simple` stores a job as a specstar resource, so it is already inside
    the specstar source; `rabbitmq` keeps it in the broker, and queued work is
    not data worth restoring. Both are fine. A third kind that persisted
    somewhere else would make the exclusion wrong — and would boot, and would be
    silently unbacked, with CI green. So the set is pinned rather than the
    conclusion.
    """
    settings = Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        message_queue=MessageQueueSettings(kind=kind),
    )
    sources = durable_sources(settings)

    assert [s.name for s in sources] == ["specstar"], (
        f"message_queue.kind={kind!r} changed what the backup covers — if this "
        "kind persists jobs outside specstar, it needs a source and a handler"
    )
