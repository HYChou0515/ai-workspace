"""Warming third-party tool bundles at boot does not hold the pod's startup.

At one boot the warm's request landed on a sandbox host that died while
handling it; with no deadline the lifespan waited 404 s for the connection to
be cut while every other step took 0.2 s. Warming is best effort and never
part of readiness (#674 Q17), so it runs in the background: the app is ready
and serving while it is still going.
"""

from __future__ import annotations

import asyncio
import threading

from tests.api._client import TestClient
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


class _StuckHost(MockSandbox):
    """A hosted backend whose tool resolve never answers."""

    resolves_tools = True

    def __init__(self) -> None:
        super().__init__()
        self.asked = threading.Event()

    async def resolve_tools(self, declared: dict[str, str]) -> dict:
        self.asked.set()
        await asyncio.Event().wait()  # never answers
        return {}


def test_a_host_that_never_answers_does_not_hold_the_boot(monkeypatch):
    import workspace_app.api.app as app_mod

    monkeypatch.setattr(
        app_mod, "_declared_external_tools", lambda: {"rca": {"wafer-history": "u"}}
    )
    host = _StuckHost()
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=host,
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()]),
    )
    done = threading.Event()
    errors: list[BaseException] = []

    def boot_and_ask() -> None:
        try:
            with TestClient(app) as client:
                # Ready — what the k8s readinessProbe asks — while the warm is stuck.
                assert host.asked.wait(5), "the warm never started"
                assert client.get("/readyz").status_code == 200
        except BaseException as exc:  # surfaced below, not lost in the thread
            errors.append(exc)
        done.set()

    t = threading.Thread(target=boot_and_ask, daemon=True)
    t.start()
    assert done.wait(30), "the lifespan waited on a host that never answered"
    assert not errors, errors
    t.join(5)


async def test_a_warm_that_fails_outright_is_logged_not_lost(caplog):
    """Awaited at boot, a failure outside the per-app guard (reading the apps'
    declarations) failed the boot loudly; as a background task it would sit
    unread until shutdown suppressed it. It is logged instead."""
    import logging

    from workspace_app.api.lifecycle import _warm_tools

    async def broken() -> dict[str, str]:
        raise ValueError("app.json is not valid")

    with caplog.at_level(logging.ERROR, logger="workspace_app.api.lifecycle"):
        await _warm_tools(broken)
    [rec] = [r for r in caplog.records if "warming third-party tools failed" in r.getMessage()]
    assert rec.exc_info is not None and "app.json is not valid" in str(rec.exc_info[1])
