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


def _preflight(client: TestClient, origin: str, *, method: str = "GET", headers: str = ""):
    ask = {"Origin": origin, "Access-Control-Request-Method": method}
    if headers:
        ask["Access-Control-Request-Headers"] = headers
    return client.options("/api/a/rca/items", headers=ask)


def test_a_configured_origin_may_call_us_with_credentials():
    """Credentials must be allowed explicitly: the caller's identity is the
    shared session cookie, and a cookie-bearing cross-origin request is refused
    unless the response says so."""
    r = _preflight(_client(cors_allowed_origins=[_LEGACY]), _LEGACY)

    assert r.headers.get("access-control-allow-origin") == _LEGACY
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_every_method_the_handoff_actually_uses_is_allowed():
    """GET is the ONE method this feature does not need a preflight for, so
    testing only GET tests only the case that cannot break. The handoff creates
    (POST), uploads (PUT) and records (PATCH); if any of those is refused at
    preflight the whole integration is dead in a real browser while every test
    here still passes.
    """
    client = _client(cors_allowed_origins=[_LEGACY])

    for method in ("POST", "PUT", "PATCH"):
        r = _preflight(client, _LEGACY, method=method)
        allowed = r.headers.get("access-control-allow-methods", "")
        assert r.status_code == 200, f"{method}: {r.status_code} {r.text}"
        assert r.headers.get("access-control-allow-origin") == _LEGACY, method
        # A concrete method, not a `*` escape hatch: paired with
        # allow_credentials a literal `*` is invalid to browsers anyway, so
        # accepting it here would let a real regression through.
        assert method in allowed, f"{method} not in {allowed!r}"


def test_the_content_type_header_the_handoff_sends_is_allowed():
    """The create and the record steps both post JSON, and `Content-Type:
    application/json` is not a CORS-safelisted VALUE — the browser asks for the
    header at preflight and refuses the request if the answer omits it.

    Documentation, not a guard, and labelled so nobody counts it twice.
    Starlette's `CORSMiddleware` does `sorted(SAFELISTED_HEADERS | set(...))`
    with `Content-Type` in that safelist, so this header is allowed for EVERY
    value of `allow_headers` — measured: narrowing it to `["authorization"]` or
    to `[]` leaves this test green. What it does catch (a broken method
    allowance, the layer disappearing) the two tests around it already catch.
    It stays because the browser fact is worth recording next to the preflight
    that depends on it — not because it is protecting anything."""
    client = _client(cors_allowed_origins=[_LEGACY])

    r = _preflight(client, _LEGACY, method="POST", headers="content-type")

    # The status matters as much as the header: a refused header makes the
    # preflight itself fail (400), which asserting only on the echoed
    # allow-headers value would miss.
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    allowed = r.headers.get("access-control-allow-headers", "").lower()
    assert "content-type" in allowed, allowed


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
