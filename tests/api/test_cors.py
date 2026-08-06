"""#700 — cross-origin access for an outside system's browser.

The handoff is driven from the legacy site's own page, so the call carries the
shared session cookie and arrives from a DIFFERENT origin. Without CORS the
browser refuses it before our code ever runs, which makes the whole feature
zero. Origins are configured per deployment and default to empty, so nothing
changes for a deployment that never opts in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

_LEGACY = "https://legacy-rca.example.com"


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


def _client(**kwargs) -> TestClient:
    return TestClient(
        create_app(
            spec=make_spec(default_user="u"),
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=_Runner(),
            agent_config_catalog=AgentConfigCatalog(),
            **kwargs,
        )
    )


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/me",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_a_configured_origin_may_call_us_with_credentials():
    """Credentials must be allowed explicitly: the caller's identity is the
    shared session cookie, and a cookie-bearing cross-origin request is refused
    unless the response says so."""
    r = _preflight(_client(cors_allowed_origins=[_LEGACY]), _LEGACY)

    assert r.headers.get("access-control-allow-origin") == _LEGACY
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_an_unlisted_origin_is_not_granted_access():
    """The allowance is a list, not a switch — turning it on for the legacy site
    must not open the API to every other site the user has a tab of."""
    r = _preflight(_client(cors_allowed_origins=[_LEGACY]), "https://somewhere-else.example.com")

    assert r.headers.get("access-control-allow-origin") is None


def test_without_configured_origins_no_cors_layer_exists():
    """Every existing deployment passes nothing, so it must keep behaving exactly
    as it did — no headers, no preflight handling, nothing added to the stack."""
    r = _preflight(_client(), _LEGACY)

    assert r.headers.get("access-control-allow-origin") is None
    assert r.headers.get("access-control-allow-credentials") is None
