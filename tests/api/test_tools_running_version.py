"""plan-tool-running-version P4: the tool picker says which release the LIVE
sandbox runs, and offers "close the sandbox to update" to whoever may close it.

The picker's version used to be the manifest's — the latest release — so a
sandbox created before a release ran the old bundle under a row that named the
new one. These drive the real route (`create_app`) with the real registry; the
only stand-ins are the artifact host (`resolve_item_tools`) and the live
session's record, which a sandbox create would otherwise write.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from typing import Any

import workspace_app.api.app as app_mod
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.perm.model import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import SandboxHandle
from workspace_app.tooling.external import ExternalTools, MountedTool, ToolProvenance
from workspace_app.tooling.registry import CommandInfo, PackageInfo

from ._client import TestClient as ApiTestClient

WHO = {"id": "alice"}
LATEST = "a" * 64


def _resolved() -> ExternalTools:
    return ExternalTools(
        packages=(
            PackageInfo(
                name="wafer-history",
                install_dir="../.tools/wafer-history",
                commands=(CommandInfo("trend", "Yield trend for a lot.", {}),),
                version="1.4.2",
            ),
        ),
        shas={"wafer-history": LATEST},
        provenance={"wafer-history": ToolProvenance(version="1.4.2")},
    )


def _declaring(monkeypatch) -> None:
    from workspace_app.api import tools_routes

    async def _resolve(*_a: Any, **_k: Any) -> ExternalTools:
        return _resolved()

    monkeypatch.setattr(tools_routes, "resolve_item_tools", _resolve)
    monkeypatch.setattr(
        tools_routes,
        "load_app_manifest",
        lambda slug: type(
            "M",
            (),
            {
                "agent": type(
                    "A",
                    (),
                    {
                        "tools": ["exec", "wafer-history"],
                        "external_tools": {"wafer-history": "https://g/m"},
                    },
                )
            },
        ),
    )


@contextlib.contextmanager
def _app(monkeypatch) -> Iterator[tuple[ApiTestClient, Any, Any]]:
    WHO["id"] = "alice"
    _declaring(monkeypatch)
    captured: dict[str, Any] = {}
    real = app_mod.InvestigationRegistry

    def _capture(**kw: Any):
        captured["registry"] = real(**kw)
        return captured["registry"]

    monkeypatch.setattr(app_mod, "InvestigationRegistry", _capture)
    spec = make_spec(default_user=lambda: WHO["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: WHO["id"],
    )
    with ApiTestClient(app) as client:
        yield client, spec, captured["registry"]


def _item(spec, *, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    return rm.create(RcaInvestigation(title="t", owner="alice", permission=permission)).resource_id


def _running(registry, item: str, tools: dict[str, MountedTool]) -> None:
    """The live session a sandbox create leaves behind (P2 writes exactly
    this: the handle, and what it was created with)."""
    session = asyncio.run(registry.session(item))
    session.handle = SandboxHandle(id="live")
    session.tools = tools


def _picker(client, item: str) -> dict:
    got = client.get(f"/a/rca/items/{item}/tools")
    assert got.status_code == 200, got.text
    return got.json()


def _row(body: dict) -> dict:
    return {r["key"]: r for r in body["tools"]}["wafer-history:trend"]


def test_an_older_sandbox_shows_the_release_it_runs_and_the_latest(monkeypatch):
    with _app(monkeypatch) as (client, spec, registry):
        item = _item(spec)
        _running(registry, item, {"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")})

        body = _picker(client, item)

        assert _row(body)["running_version"] == "1.3.0"
        assert _row(body)["version"] == "1.4.2"
        assert body["update_needs_close"] is True
        assert body["can_close"] is True  # the owner may close it


def test_a_sandbox_on_the_latest_release_shows_nothing_extra(monkeypatch):
    with _app(monkeypatch) as (client, spec, registry):
        item = _item(spec)
        _running(registry, item, {"wafer-history": MountedTool(sha=LATEST, version="1.4.2")})

        body = _picker(client, item)

        assert _row(body)["running_version"] is None
        assert body["update_needs_close"] is False


def test_no_live_sandbox_reads_as_the_latest(monkeypatch):
    # D4/D7: nothing running, or unknown — the next sandbox gets the latest.
    with _app(monkeypatch) as (client, spec, _registry):
        item = _item(spec)

        body = _picker(client, item)

        assert _row(body)["running_version"] is None
        assert body["update_needs_close"] is False


def test_a_reader_who_may_not_close_is_told_but_offered_no_button(monkeypatch):
    # D9: the button's gate is the close route's own — a viewer who would get
    # a 404 from it must not be handed a button that does nothing.
    with _app(monkeypatch) as (client, spec, registry):
        item = _item(spec, permission=Permission(visibility="restricted", read_meta=["user:bob"]))
        _running(registry, item, {"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")})

        WHO["id"] = "bob"
        body = _picker(client, item)

        assert body["update_needs_close"] is True
        assert body["can_close"] is False
        assert client.delete(f"/me/resources/live/{item}").status_code == 404  # the same answer


def test_a_manager_may_close_it_here_as_on_the_resources_page(monkeypatch):
    with _app(monkeypatch) as (client, spec, registry):
        item = _item(
            spec,
            permission=Permission(
                visibility="restricted",
                read_meta=["user:bob"],
                change_permission=["user:bob"],
            ),
        )
        _running(registry, item, {"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")})

        WHO["id"] = "bob"

        assert _picker(client, item)["can_close"] is True


def test_a_live_sandbox_with_no_record_reads_as_the_latest(monkeypatch):
    # D4 with a sandbox running: built before the record existed — unknown.
    with _app(monkeypatch) as (client, spec, registry):
        item = _item(spec)
        session = asyncio.run(registry.session(item))
        session.handle = SandboxHandle(id="live")
        session.tools = None

        body = _picker(client, item)

        assert _row(body)["running_version"] is None
        assert body["update_needs_close"] is False


def test_a_tool_the_sandbox_was_built_without_offers_the_same_close(monkeypatch):
    # D11: a tool added to the app after the sandbox was created is not in it;
    # closing the sandbox is what fixes that too (review round 1).
    with _app(monkeypatch) as (client, spec, registry):
        item = _item(spec)
        _running(registry, item, {})

        body = _picker(client, item)

        assert _row(body)["not_in_sandbox"] is True
        assert _row(body)["running_version"] is None
        assert body["update_needs_close"] is True


def test_a_host_error_never_costs_the_picker(monkeypatch):
    # Review round 1: the picker made no sandbox call before; a host that
    # errors (or hangs) must read as unknown, not 500 the picker and every
    # view that shares its query.
    import httpx

    class _Published:
        handle = SandboxHandle(id="live")
        tools = {"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")}

    class _Address:
        async def published(self, _item):
            return _Published()

    with _app(monkeypatch) as (client, spec, registry):
        item = _item(spec)
        _running(registry, item, {"wafer-history": MountedTool(sha="b" * 64, version="1.3.0")})
        registry.address = _Address()

        async def boom(_handle, _path):
            raise httpx.HTTPStatusError(
                "503", request=httpx.Request("GET", "http://h"), response=httpx.Response(503)
            )

        registry.sandbox.exists = boom

        body = _picker(client, item)

        assert _row(body)["running_version"] is None
        assert body["update_needs_close"] is False
