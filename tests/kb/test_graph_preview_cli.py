"""#697 — the preview must not blame the collection for a missing config.

`load()` resolves `./config.yaml` relative to the CURRENT DIRECTORY, and falls
back to bundled defaults when it finds nothing. Those defaults configure no
store at all, so specstar uses its in-memory one — which is empty by
construction. Every collection id then comes back "not found", and the error
names the ID, so the first thing anyone checks is the one thing that was right.

The check below is what turns that into a sentence about the config.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from workspace_app.config.schema import Settings
from workspace_app.graph_preview import unusable_config


def _with_store(**kw) -> Settings:
    settings = Settings()
    return dataclasses.replace(settings, filestore=dataclasses.replace(settings.filestore, **kw))


def test_no_store_configured_is_refused_before_anything_runs():
    reason = unusable_config(Settings(), None)
    assert reason
    assert "config.yaml" in reason
    # it has to say what to DO, not merely that something is wrong
    assert "--config" in reason


def test_the_refusal_names_the_file_that_was_actually_read():
    """A config that WAS found but configures no store is a different mistake
    from no config at all, and saying which one it is decides where to look."""
    reason = unusable_config(Settings(), Path("/etc/ws/config.yaml"))
    assert reason and "/etc/ws/config.yaml" in reason


def test_a_disk_backed_config_is_usable():
    assert unusable_config(_with_store(disk_root="/srv/data"), Path("config.yaml")) is None


def test_a_postgres_backed_config_is_usable():
    assert unusable_config(_with_store(pg_dsn="postgresql://x/y"), Path("config.yaml")) is None
