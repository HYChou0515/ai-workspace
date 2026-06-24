"""GET /health/checks + POST /health/checks/run (#51 P3).

The FE diagnostics page reads one shape: every registered check (even
never-run ones — the page lists all seven from first paint) with its
latest result when available, plus the global `running` flag. The run
endpoint triggers a round (all or one check) asynchronously and is
refused while one is in flight.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.health import CheckRegistry, CheckResult, ISanityCheck
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if False:
            yield  # pragma: no cover


class _Static(ISanityCheck):
    def __init__(self, check_id: str, *, fast: bool = False, status: str = "pass") -> None:
        self.check_id = check_id
        self.description = f"{check_id} probe"
        self.fast = fast
        self._status = status

    def run(self) -> CheckResult:
        return CheckResult(check_id=self.check_id, status=self._status, detail="probed")


def _wait_until_settled(client: TestClient, timeout: float = 5.0) -> dict:
    """Poll GET /health/checks until no round is running and every
    check has a result — startup kicks an async full round, so tests
    must wait for it like the FE does."""
    import time

    deadline = time.monotonic() + timeout
    while True:
        body = client.get("/health/checks").json()
        if not body["running"] and all(r["status"] is not None for r in body["checks"]):
            return body
        assert time.monotonic() < deadline, f"checks never settled: {body}"
        time.sleep(0.02)


def _client(registry: CheckRegistry | None) -> TestClient:
    app = create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        check_registry=registry,
    )
    return TestClient(app)


def test_get_checks_lists_registered_checks_even_before_any_run():
    reg = CheckRegistry().register(_Static("conn", fast=True)).register(_Static("capability"))
    # TestClient's context manager runs the lifespan (startup fast-sync
    # + the async full round) — enter WITHOUT the context manager so we
    # see the pre-run shape.
    client = _client(reg)
    body = client.get("/health/checks").json()
    assert body["running"] is False
    rows = {r["check_id"]: r for r in body["checks"]}
    assert set(rows) == {"conn", "capability"}
    assert rows["conn"]["fast"] is True
    assert rows["conn"]["description"] == "conn probe"
    # Never run → no result fields yet.
    assert rows["conn"]["status"] is None
    assert rows["capability"]["checked_at"] is None


def test_startup_runs_fast_sync_then_full_round():
    reg = (
        CheckRegistry()
        .register(_Static("conn", fast=True))
        .register(_Static("capability", status="fail"))
    )
    with _client(reg) as client:
        # Lifespan ran the fast set synchronously and kicked the async
        # full round — poll until it settles (as the FE does).
        body = _wait_until_settled(client)
        rows = {r["check_id"]: r for r in body["checks"]}
        assert rows["conn"]["status"] == "pass"
        assert rows["capability"]["status"] == "fail"
        assert rows["capability"]["detail"] == "probed"
        assert rows["capability"]["checked_at"] > 0


def test_post_run_triggers_a_round_and_single_check_mode():
    reg = CheckRegistry().register(_Static("conn", fast=True)).register(_Static("capability"))
    with _client(reg) as client:
        _wait_until_settled(client)  # let the startup round finish first
        resp = client.post("/health/checks/run", json={})
        assert resp.status_code == 202
        assert resp.json()["started"] is True

        resp = client.post("/health/checks/run", json={"check_id": "capability"})
        assert resp.status_code == 202

        # Unknown check_id → 404, not a silent empty round.
        resp = client.post("/health/checks/run", json={"check_id": "nope"})
        assert resp.status_code == 404


def test_check_runs_persist_for_history():
    """Every executed probe lands a CheckRun row — the audit trail for
    "when did this stop passing?"."""
    from specstar import QB

    from workspace_app.resources import CheckRun

    spec = make_spec()
    reg = CheckRegistry().register(_Static("conn", fast=True))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        check_registry=reg,
    )
    with TestClient(app):
        rm = spec.get_resource_manager(CheckRun)
        rows = [r.data for r in rm.list_resources((QB["check_id"] == "conn").build())]
        # startup fast-sync + the async full round each ran it once.
        assert len(rows) >= 1
        assert all(r.status == "pass" for r in rows)  # ty: ignore[unresolved-attribute]


def test_no_registry_serves_an_empty_panel():
    """create_app without a check registry (tests / minimal deploys)
    still serves the endpoint — empty list, never a 500."""
    with _client(None) as client:
        body = client.get("/health/checks").json()
        assert body == {"running": False, "checks": []}
