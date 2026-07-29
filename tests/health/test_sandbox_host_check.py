"""The diagnostics probe that answers "is the sandbox host running the code we
think it is?" — the question that cost two full diagnoses.

An old sandbox-host answers every request perfectly well; it just behaves like
the old code. Nothing in the app could tell the difference, so a tool that needs
`$HOME` failed in the sandbox while every health signal stayed green.
"""

from __future__ import annotations

import httpx

from workspace_app.health.checks.sandbox_host import SandboxHostCapabilityCheck


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://host")


def test_skips_when_the_sandbox_is_not_the_http_host():
    """`kind: local`/mock has no host to interrogate — skip, not fail, so the
    diagnostics page doesn't cry wolf on a laptop."""
    check = SandboxHostCapabilityCheck(base_url="")
    result = check.run()
    assert result.status == "skip"


def test_passes_when_the_host_advertises_everything_we_need():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "version": "0.1.0",
                "capabilities": ["host-managed-archive", "per-exec-home"],
            },
        )

    check = SandboxHostCapabilityCheck(
        base_url="http://host", client_factory=lambda: _client(handler)
    )
    result = check.run()
    assert result.status == "pass"
    assert "0.1.0" in result.detail


def test_fails_and_names_the_missing_capability_and_the_remedy():
    """The failure text is the whole point: it has to say what is missing AND
    that the fix is a rebuilt image, because the app's own code is fine and a
    reader will otherwise go looking for a bug that isn't there."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "capabilities": []})

    check = SandboxHostCapabilityCheck(
        base_url="http://host", client_factory=lambda: _client(handler)
    )
    result = check.run()
    assert result.status == "fail"
    assert "per-exec-home" in result.detail
    assert "rebuild" in result.detail.lower()


def test_an_old_host_without_the_field_at_all_is_reported_as_stale():
    """A host that predates the advertisement answers `{"status": "ok"}` — the
    absence of the field is itself the signal, not a reason to pass."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    check = SandboxHostCapabilityCheck(
        base_url="http://host", client_factory=lambda: _client(handler)
    )
    result = check.run()
    assert result.status == "fail"
    assert "rebuild" in result.detail.lower()


def test_an_unreachable_host_is_an_error_not_a_failure():
    """Wiring problem vs behaviour problem: `error` points at connectivity,
    `fail` at the image. Conflating them sends the reader to the wrong place."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    check = SandboxHostCapabilityCheck(
        base_url="http://host", client_factory=lambda: _client(handler)
    )
    result = check.run()
    assert result.status == "error"
