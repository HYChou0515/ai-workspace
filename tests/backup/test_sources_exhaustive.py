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
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)

_FACTORIES = pathlib.Path(factories_module.__file__)


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


def test_the_extractor_still_reads_the_arms_it_is_supposed_to():
    """Positive control for the guard itself. Without this a `match` → `if/elif`
    refactor would leave every parametrised case below with nothing to iterate,
    and the suite would go green on zero coverage."""
    assert "specstar" in FILESTORE_KINDS
    assert "nfs_tree" in DURABLE_KINDS
    assert "" in DURABLE_KINDS  # the "follow the API filestore" alias
    assert "specstar" in MIGRATE_FROMS


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
