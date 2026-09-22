"""`durable_sources` is invariant one of `docs/plan-backup.md`: the set of stores a
backup covers is DERIVED from the same `Settings` the app boots from, so no config
shape can leave a store silently unbacked.

The trap this exists for: `sandbox.durable.kind: ""` does not mean "no sandbox
data". It means "the sandbox writes into the API filestore" — the same store as
`filestore.kind`. A hand-maintained path list gets that one backwards and either
backs the same tree up twice or misses both.
"""

import pytest

from workspace_app.backup import UnsupportedDeployment, durable_sources
from workspace_app.config.schema import (
    FilestoreSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)


def _settings(*, filestore: FilestoreSettings, durable: SandboxDurableSettings) -> Settings:
    return Settings(filestore=filestore, sandbox=SandboxSettings(durable=durable))


def test_nfs_tree_deployment_covers_the_specstar_store_and_the_workspace_tree():
    """Production's shape: two stores, and the workspace bytes are in the tree
    rather than in specstar's blob pool."""
    settings = _settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        durable=SandboxDurableSettings(kind="nfs_tree", nfs_root="/mnt/workspaces"),
    )

    sources = durable_sources(settings)

    assert [(s.name, s.kind, s.root) for s in sources] == [
        ("specstar", "specstar", "/data"),
        ("sandbox-workspaces", "tree", "/mnt/workspaces"),
    ]


def test_default_durable_kind_is_one_store_not_two_and_not_none():
    """The aliasing trap. `sandbox.durable.kind: ""` means the sandbox writes into
    the API filestore, so the workspace files ARE covered — by the specstar source,
    once. Two sources here would archive the same store twice; zero would lose the
    workspaces."""
    settings = _settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        durable=SandboxDurableSettings(kind=""),
    )

    sources = durable_sources(settings)

    assert [s.name for s in sources] == ["specstar"]


def test_migrate_from_specstar_says_both_halves_hold_workspace_files():
    """M2 dual-read: the tree is primary and specstar is the fallback that still
    holds anything not yet backfilled. Coverage needs no third source — specstar is
    already source one — but a restore has to know, so it is stated."""
    settings = _settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        durable=SandboxDurableSettings(
            kind="nfs_tree", nfs_root="/mnt/workspaces", migrate_from="specstar"
        ),
    )

    sources = durable_sources(settings)

    assert [s.name for s in sources] == ["specstar", "sandbox-workspaces"]
    assert "migrate_from: specstar" in sources[1].why


def test_memory_filestore_contributes_no_source():
    """A legal kind that persists nothing. Answering "no durable store" is the
    correct answer; the caller refuses to write an archive rather than writing an
    empty one."""
    settings = _settings(
        filestore=FilestoreSettings(kind="memory"),
        durable=SandboxDurableSettings(kind=""),
    )

    assert durable_sources(settings) == ()


def test_specstar_without_a_backend_contributes_no_source():
    """`filestore.kind: specstar` with neither `disk_root` nor `pg_dsn` is what
    `factories._backend_for` turns into specstar's in-memory default — it boots and
    persists nothing."""
    settings = _settings(
        filestore=FilestoreSettings(kind="specstar"),
        durable=SandboxDurableSettings(kind=""),
    )

    assert durable_sources(settings) == ()


def test_an_unhandled_filestore_kind_raises_rather_than_covering_less():
    settings = _settings(
        filestore=FilestoreSettings(kind="cassandra", disk_root="/data"),
        durable=SandboxDurableSettings(kind=""),
    )

    with pytest.raises(UnsupportedDeployment, match="filestore.kind: 'cassandra'"):
        durable_sources(settings)


def test_an_unhandled_durable_kind_raises_rather_than_covering_less():
    settings = _settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        durable=SandboxDurableSettings(kind="s3_tree"),
    )

    with pytest.raises(UnsupportedDeployment, match="sandbox.durable.kind: 's3_tree'"):
        durable_sources(settings)


def test_an_unhandled_migrate_from_raises_rather_than_covering_less():
    settings = _settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        durable=SandboxDurableSettings(
            kind="nfs_tree", nfs_root="/mnt/workspaces", migrate_from="mongo"
        ),
    )

    with pytest.raises(UnsupportedDeployment, match="migrate_from: 'mongo'"):
        durable_sources(settings)


def test_nfs_tree_without_a_root_raises():
    """`factories.get_sandbox_filestore` refuses this, so the app never booted —
    but the backup must not answer "one source" for a config that cannot run."""
    settings = _settings(
        filestore=FilestoreSettings(kind="specstar", disk_root="/data"),
        durable=SandboxDurableSettings(kind="nfs_tree"),
    )

    with pytest.raises(UnsupportedDeployment, match="nfs_root"):
        durable_sources(settings)


def test_a_naive_since_is_read_as_utc_rather_than_exploding_later():
    """`--since 2026-01-01` is what an operator types. It parses to a NAIVE
    datetime, which then meets an aware one inside the run and raises
    `TypeError: can't compare offset-naive and offset-aware datetimes` — a
    traceback about datetimes for what is really "you left the timezone off"."""
    from workspace_app.backup.run import parse_since

    assert parse_since("2026-01-01").tzinfo is not None
    offset = parse_since("2026-01-01T00:00:00+08:00").utcoffset()
    assert offset is not None and offset.total_seconds() == 8 * 3600
