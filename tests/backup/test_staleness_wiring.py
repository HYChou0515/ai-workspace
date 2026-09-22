"""The staleness probe has to be REACHED, not merely written.

A sweeper that exists and is never launched is the most expensive kind of guard:
it reads as covered in review, it has tests, and the thing it watches goes
unwatched. So this drives the real composition root and waits for the loop to
tick, rather than calling the closure directly.

`with TestClient(app)` is what enters the lifespan — a bare `TestClient(app)`
never starts the background tasks, and a test written that way would pass
against an app that launches nothing.
"""

from __future__ import annotations

import time
from datetime import timedelta

from starlette.testclient import TestClient

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.config.schema import BackupSettings, Settings
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _app(**kwargs):
    return create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        **kwargs,
    )


def test_the_lifespan_actually_runs_the_staleness_probe():
    calls: list[int] = []

    app = _app(
        backup_staleness=lambda: calls.append(1) or 0,
        backup_staleness_interval=timedelta(milliseconds=10),
    )

    with TestClient(app):
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.02)

    assert calls, "the lifespan started but never ran the backup-staleness probe"


def test_a_deploy_without_a_destination_gets_no_probe_at_all():
    """`None` is the absence of the feature, not a degraded mode — and the gate
    is in `__main__`, where the settings are.

    This used to be a test with no assertions, which meant it stayed green under
    every mutation and proved nothing while being described as a control. The
    discriminating question is whether the probe is built at all, so that is what
    it asks now — both ways, because "always returns None" would satisfy half of
    it.
    """
    from workspace_app.__main__ import _backup_staleness_probe

    spec = make_spec()

    off = Settings(backup=BackupSettings(dest=""))
    on = Settings(backup=BackupSettings(dest="/backups"))

    assert _backup_staleness_probe(off, spec) is None
    assert callable(_backup_staleness_probe(on, spec))


def test_the_lifespan_is_clean_when_no_probe_is_configured():
    """The gate has to be on the right side of the `if`: a deploy that never
    opted in must still boot, serve and shut down."""
    app = _app(backup_staleness=None, backup_staleness_interval=timedelta(milliseconds=10))

    with TestClient(app) as client:
        assert client.get("/api/readyz").status_code in (200, 503)
