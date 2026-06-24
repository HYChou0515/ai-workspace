"""#89 P4a — the additive App read endpoints the launcher + dashboard consume.

`GET /apps` lists launcher-card summaries; `GET /apps/{slug}` returns the full
manifest. Pure reads off the on-disk app.json — no live behaviour change.
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


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


def _client() -> TestClient:
    return TestClient(
        create_app(
            spec=make_spec(default_user="u"),
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=_Runner(),
            agent_config_catalog=AgentConfigCatalog(),
        )
    )


def test_get_apps_lists_launcher_summaries():
    apps = _client().get("/apps").json()
    rca = next(a for a in apps if a["slug"] == "rca")
    assert rca["title"] == "Root Cause Analysis"
    assert rca["icon"] == "flame"
    assert rca["color"]  # a hex for the per-card accent
    assert "description" in rca
    # summary is lean — no agent/layout internals
    assert "agent" not in rca and "layout" not in rca


def test_get_app_manifest_returns_the_full_manifest():
    m = _client().get("/apps/rca").json()
    assert m["item"]["create_label"] == "Start Investigation"
    assert m["function"]["sandbox"] is True
    assert [p["preset"] for p in m["agent"]["picker"]] == [
        "qwen3-local",
        "claude-opus",
        "openai-mini",
    ]
    assert m["layout"]["list"] == ["severity", "status", "product"]
    assert m["labels"]["severity"] == "Severity"
    # the specstar CRUD route the FE lists/gets this App's items from
    assert m["resource_route"] == "/rca-investigation"


def test_get_app_manifest_includes_the_field_schema():
    """The manifest carries each domain field's render kind + enum options,
    projected from the App's model — so the FE renders + inline-edits them
    without restating types. `severity` is a select with its enum values;
    `product` is a plain text field with no options."""
    m = _client().get("/apps/rca").json()
    by_name = {f["name"]: f for f in m["fields"]}
    assert by_name["severity"]["kind"] == "select"
    assert by_name["severity"]["options"] == ["P0", "P1", "P2", "P3", "P4"]
    assert by_name["product"]["kind"] == "text"
    assert "options" not in by_name["product"]  # UNSET omitted on the wire


def test_get_app_manifest_includes_field_style_overlay():
    """`field_styles` maps an enum field's options to tone tokens (the FE chip
    colours), so RCA's `severity`/`status` palette is DATA, not shell code."""
    m = _client().get("/apps/rca").json()
    assert m["field_styles"]["severity"]["P0"] == "err"
    assert m["field_styles"]["status"]["resolved"] == "ok"


def test_get_app_manifest_includes_lifecycle_and_default_tabs():
    """`lifecycle` declares the App's close workflow (which states close it);
    `default_tabs` lists the files the workspace opens on entry — both data, so
    the shell's Close affordance + initial tabs aren't RCA-hardcoded. No canvas
    / 5-Why tabs survive."""
    m = _client().get("/apps/rca").json()
    assert m["lifecycle"]["status_field"] == "status"
    assert m["lifecycle"]["closing_states"] == ["resolved", "abandoned"]
    assert "/SOP.md" in m["layout"]["default_tabs"]
    assert not any("canvas" in t or "5-why" in t for t in m["layout"]["default_tabs"])


def test_get_app_manifest_includes_the_profile_list():
    """The create flow's profile picker needs the App's profiles (name + title +
    description), projected from apps.profiles + folded into the manifest."""
    m = _client().get("/apps/rca").json()
    by_name = {p["name"]: p for p in m["profiles"]}
    assert {"default", "tool-demo", "local-lab", "smt-reflow-example"} <= set(by_name)
    assert "methodology" not in by_name  # the 5-Why/canvas profile was dropped
    assert by_name["local-lab"]["title"]  # carries a display title for the picker
    assert m["default_profile"] == "default"


def test_get_app_manifest_carries_onboarding_teaching():
    """#161 — the per-App welcome teaching flows to the FE through the manifest
    endpoint (version + title + read-only points)."""
    ob = _client().get("/apps/rca").json()["onboarding"]
    assert ob is not None
    assert ob["version"]
    assert ob["title"]
    assert len(ob["points"]) >= 2
    assert all(p["title"] and p["body"] for p in ob["points"])


def test_get_app_manifest_unknown_slug_404():
    assert _client().get("/apps/nope").status_code == 404


def test_post_app_item_creates_the_resource_and_seeds_the_profile():
    from workspace_app.apps.rca.model import RcaInvestigation, Severity

    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
    )
    r = TestClient(app).post(
        "/a/rca/items",
        json={
            "title": "Oven drift",
            "severity": "P1",
            "product": "MX-7",
            "description": "voids",
            "owner": "hacker",  # must be ignored — owner comes from auth
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "/SOP.md" in body["seeded"]  # the default profile was seeded

    got = spec.get_resource_manager(RcaInvestigation).get(body["resource_id"]).data
    assert got.title == "Oven drift"
    assert got.severity is Severity.P1
    assert got.owner == "default-user"  # from auth, NOT the body's "hacker"
    assert got.profile == "default"  # the App's default_profile


def test_post_app_item_unknown_slug_404():
    assert _client().post("/a/nope/items", json={"title": "x"}).status_code == 404
