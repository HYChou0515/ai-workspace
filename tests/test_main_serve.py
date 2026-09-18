"""`serve` — what `python -m workspace_app` hands the app to (plan-graceful-
shutdown P2). It replaced `uvicorn.run`, and must keep what `uvicorn.run` did
beside starting the server: a boot that never started exits 3, and Ctrl-C
before the handlers are installed is a clean stop, not a traceback."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI

from workspace_app.__main__ import STARTUP_FAILURE, serve
from workspace_app.api.drain import Drain


def _app_that_cannot_start() -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        raise RuntimeError("the store refused")
        yield  # noqa: RET503 — unreachable, keeps the generator shape

    app = FastAPI(lifespan=lifespan)
    app.state.drain = Drain()
    return app


def test_serve_exits_3_when_the_app_never_started() -> None:
    """`uvicorn.run` ended with `sys.exit(STARTUP_FAILURE)` for a server that
    never started; `Server.run` alone returns normally, so a pod with a bad
    config reported "Completed" (round 1). Port 0: nothing is bound anyway —
    the lifespan fails before the listener opens."""
    with pytest.raises(SystemExit) as exc:
        serve(_app_that_cannot_start(), host="127.0.0.1", port=0, shutdown_budget_sec=1)
    assert exc.value.code == STARTUP_FAILURE == 3
