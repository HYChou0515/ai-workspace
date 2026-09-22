"""The drill's refusals, which are the only part of it that can cause harm.

`scripts/backup_drill.py` restores into whatever `--target-config` names, so a
target config that points at the source's store turns a rehearsal into an
outage. The two configs differ by one line and that line is easy to forget when
copying a file, which is exactly why the check is code rather than a warning in
a docstring.

The drill itself is not unit-tested — it exists to be run against a real
deployment, and a version of it that passed on `tmp_path` would be testing the
same thing `test_restore.py` already does. Its guards are another matter.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from workspace_app.config.schema import (
    BackupSettings,
    FilestoreSettings,
    SandboxDurableSettings,
    SandboxSettings,
    Settings,
)

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "backup_drill.py"


def _drill():
    spec = importlib.util.spec_from_file_location("backup_drill", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_drill"] = module
    spec.loader.exec_module(module)
    return module


def _settings(*, disk_root: str, dest: str, tree: str = "") -> Settings:
    durable = (
        SandboxDurableSettings(kind="nfs_tree", nfs_root=tree)
        if tree
        else SandboxDurableSettings(kind="")
    )
    return Settings(
        filestore=FilestoreSettings(kind="specstar", disk_root=disk_root),
        sandbox=SandboxSettings(durable=durable),
        backup=BackupSettings(dest=dest),
    )


def test_it_refuses_to_restore_onto_the_deployment_it_just_backed_up():
    drill = _drill()
    same = _settings(disk_root="/data", dest="/backups")

    with pytest.raises(SystemExit, match="same filestore.disk_root"):
        drill._refuse_same_store(same, same)


def test_it_refuses_configs_that_do_not_share_a_destination():
    """Otherwise the restore reads a directory the backup never wrote to, finds
    an older chain or nothing, and reports whatever it finds as the result."""
    drill = _drill()
    source = _settings(disk_root="/data", dest="/backups")
    target = _settings(disk_root="/restored", dest="/elsewhere")

    with pytest.raises(SystemExit, match="different backup.dest"):
        drill._refuse_same_store(source, target)


def test_it_refuses_a_shared_workspace_tree():
    """The specstar halves can differ while the trees still collide — and the
    tree is extracted over whatever is there."""
    drill = _drill()
    source = _settings(disk_root="/data", dest="/backups", tree="/mnt/workspaces")
    target = _settings(disk_root="/restored", dest="/backups", tree="/mnt/workspaces")

    with pytest.raises(SystemExit, match="same workspace tree"):
        drill._refuse_same_store(source, target)


def test_a_properly_separated_pair_is_allowed():
    """The positive control. Without it every assertion above would also pass on
    a guard that refuses everything."""
    drill = _drill()
    source = _settings(disk_root="/data", dest="/backups", tree="/mnt/workspaces")
    target = _settings(disk_root="/restored", dest="/backups", tree="/mnt/restored-workspaces")

    drill._refuse_same_store(source, target)  # must not raise
