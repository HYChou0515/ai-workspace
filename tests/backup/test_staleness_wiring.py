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


def test_a_deploy_without_a_destination_starts_no_loop():
    """`None` is the absence of the feature, not a degraded mode. A timer that
    wakes forever to decide it has nothing to say is a loop nobody asked for."""
    app = _app(backup_staleness=None, backup_staleness_interval=timedelta(milliseconds=10))

    with TestClient(app):
        time.sleep(0.1)

    # Nothing to assert on the probe itself; what matters is that entering and
    # leaving the lifespan with no probe configured is clean — a crash here would
    # mean the gate is on the wrong side of the `if`.
