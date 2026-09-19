"""#89 P4a — the additive App read endpoints the launcher + dashboard consume.

`GET /apps` lists launcher-card summaries; `GET /apps/{slug}` returns the full
manifest. Pure reads off the on-disk app.json — no live behaviour change.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import msgspec

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.apps.manifest import load_app_manifest
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


def test_get_app_manifest_profile_carries_upload_dir():
    """#198: each profile entry exposes its ``upload_dir`` so the FE chat attach
    knows which folder to stage files into (resolved from the item's profile)."""
    by_name = {p["name"]: p for p in _client().get("/apps/rca").json()["profiles"]}
    assert by_name["default"]["upload_dir"] == "uploads"


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
    assert got.owner == "u"  # from auth (spec default_user), NOT the body's "hacker"
    assert got.profile == "default"  # the App's default_profile


def test_post_app_item_still_creates_when_seeding_fails(monkeypatch):
    # Regression: the item ROW is created BEFORE its starter files are seeded, so a
    # seeding failure used to 500 *after* the item existed — the client saw an error
    # and never navigated, yet the orphan item lingered. That's the "pressed Create,
    # nothing happened; it only showed up after a refresh" symptom. Seeding is
    # best-effort now: the create still returns the id so the FE navigates into it.
    from workspace_app.apps.rca.model import RcaInvestigation

    async def _boom(*_a, **_k):
        raise RuntimeError("filestore exploded mid-seed")

    monkeypatch.setattr("workspace_app.apps.seeding.seed_item", _boom)

    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
    )
    r = TestClient(app).post("/a/rca/items", json={"title": "Oven drift"})

    assert r.status_code == 200  # NOT a 500 — the create still lands
    body = r.json()
    assert body["resource_id"]
    assert body["seeded"] == []  # seeding blew up, but the item exists
    got = spec.get_resource_manager(RcaInvestigation).get(body["resource_id"]).data
    assert got.title == "Oven drift"  # the row was persisted


def test_post_app_item_unknown_slug_404():
    assert _client().post("/a/nope/items", json={"title": "x"}).status_code == 404


# ── file-based App icons: a PNG, not just an inlined SVG ──────────────────────
# A 1×1 transparent PNG — a real raster file, so the route is asserted on actual
# bytes rather than on a stub no browser could decode.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _ships_icon(tmp_path, monkeypatch, icon: str, blob: bytes | None = None) -> str:
    """Re-point the App loaders at a temp tree in which the real ``rca`` App
    declares `icon` in its manifest, shipping `blob` under that name when given.

    A REAL slug, so the route's own unknown-app guard (and app startup, which
    imports each App's model) still runs against the shipped package — only
    where the icon is READ FROM moves."""
    slug = "rca"
    (tmp_path / slug).mkdir()
    if blob is not None:
        (tmp_path / slug / icon).write_bytes(blob)
    declared = msgspec.structs.replace(load_app_manifest(slug), icon=icon)
    monkeypatch.setattr("workspace_app.apps.manifest.load_app_manifest", lambda _s: declared)
    monkeypatch.setattr("workspace_app.apps.manifest.apps_root", lambda: tmp_path)
    return slug


def test_get_app_icon_serves_a_shipped_png(tmp_path, monkeypatch):
    """An App may ship its icon as a PNG; the route hands the browser the real
    bytes with the right media type, so `<img>` can render it."""
    client = _client()
    slug = _ships_icon(tmp_path, monkeypatch, "icon.png", _PNG_1X1)

    r = client.get(f"/apps/{slug}/icon")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == _PNG_1X1


def test_get_app_icon_serves_a_shipped_svg_as_a_file(tmp_path, monkeypatch):
    """SVG travels the same road as PNG — a URL with an image media type, not
    markup folded into the manifest JSON. One rule for every file icon."""
    client = _client()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'
    slug = _ships_icon(tmp_path, monkeypatch, "icon.svg", svg)

    r = client.get(f"/apps/{slug}/icon")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.content == svg


def test_get_app_manifest_reports_a_file_icon_by_name_not_by_content(tmp_path, monkeypatch):
    """The manifest keeps naming the FILE; it never carries the picture. That is
    what lets the launcher list and the app detail show the same icon — the list
    endpoint was never able to inline anything."""
    client = _client()
    slug = _ships_icon(tmp_path, monkeypatch, "icon.svg", b"<svg/>")

    assert client.get(f"/apps/{slug}").json()["icon"] == "icon.svg"
    assert next(a for a in client.get("/apps").json() if a["slug"] == slug)["icon"] == "icon.svg"


def test_get_app_icon_404s_when_the_app_ships_no_file(tmp_path, monkeypatch):
    """A named-icon key (`flame`) is not a file — the FE draws the named glyph,
    so there is nothing to serve here."""
    client = _client()
    slug = _ships_icon(tmp_path, monkeypatch, "flame")

    assert client.get(f"/apps/{slug}/icon").status_code == 404


def test_get_app_icon_404s_when_the_named_file_is_missing(tmp_path, monkeypatch):
    """A manifest naming a file that isn't shipped degrades to the FE's fallback
    glyph — NOT a 500. This is the shape that used to break: a binary named
    `.svg` was read as utf-8 text and took the whole manifest endpoint down."""
    client = _client()
    slug = _ships_icon(tmp_path, monkeypatch, "icon.png")  # declared, never written

    assert client.get(f"/apps/{slug}/icon").status_code == 404
    assert client.get(f"/apps/{slug}").status_code == 200  # manifest still serves


def test_get_app_icon_404s_on_an_unservable_extension(tmp_path, monkeypatch):
    """Only image types are served. An App cannot turn this route into a reader
    for arbitrary files in its own directory."""
    client = _client()
    slug = _ships_icon(tmp_path, monkeypatch, "app.json", b'{"secret": true}')

    assert client.get(f"/apps/{slug}/icon").status_code == 404


def test_get_app_icon_refuses_to_leave_the_app_directory(tmp_path, monkeypatch):
    """An icon is a plain filename beside app.json. A manifest carrying a path
    is refused rather than resolved, so it can never reach a sibling App's (or
    the deploy's) files."""
    client = _client()
    (tmp_path / "secret.png").write_bytes(_PNG_1X1)
    slug = _ships_icon(tmp_path, monkeypatch, "../secret.png")

    assert client.get(f"/apps/{slug}/icon").status_code == 404


def test_get_app_icon_unknown_slug_404():
    assert _client().get("/apps/nope/icon").status_code == 404


# ── `GET /apps/{slug}/assets/{name}` — the images an App's onboarding embeds ──


def _ships_assets(tmp_path, monkeypatch, files: dict[str, bytes]) -> str:
    """Re-point the App loaders at a temp tree in which the real ``rca`` App
    ships ``files`` under ``assets/`` (the folder onboarding markdown refers to
    as ``assets/<name>``). Same shape as ``_ships_icon``: a REAL slug so the
    route's unknown-app guard still runs against the package."""
    slug = "rca"
    (tmp_path / slug / "assets").mkdir(parents=True)
    for name, blob in files.items():
        (tmp_path / slug / "assets" / name).write_bytes(blob)
    monkeypatch.setattr("workspace_app.apps.manifest.apps_root", lambda: tmp_path)
    return slug


def test_get_app_asset_serves_a_shipped_image(tmp_path, monkeypatch):
    """`![](assets/hero.png)` in an App's onboarding resolves to this route; the
    browser gets the real bytes with the right media type."""
    client = _client()
    slug = _ships_assets(tmp_path, monkeypatch, {"hero.png": _PNG_1X1})

    r = client.get(f"/apps/{slug}/assets/hero.png")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == _PNG_1X1


def test_get_app_asset_404s_when_the_file_is_missing(tmp_path, monkeypatch):
    client = _client()
    slug = _ships_assets(tmp_path, monkeypatch, {})

    assert client.get(f"/apps/{slug}/assets/hero.png").status_code == 404


def test_get_app_asset_serves_only_image_types(tmp_path, monkeypatch):
    """Same allowlist as the icon: the folder may hold anything, the route hands
    out images only — it is not a reader for arbitrary files an App ships."""
    client = _client()
    slug = _ships_assets(tmp_path, monkeypatch, {"notes.txt": b"secret"})

    assert client.get(f"/apps/{slug}/assets/notes.txt").status_code == 404


def test_get_app_asset_route_never_sees_a_path_shaped_name(tmp_path, monkeypatch):
    """What the ROUTER does with a traversal-shaped URL: an encoded `/` in the
    name segment matches no route, so the handler is never called (probed with
    a spy on the loader) and the answer is the router's 404. A literal `../`
    is not exercised here because the HTTP client normalises it away before
    the request leaves (`/apps/rca/assets/../icon.png` is sent as
    `/apps/rca/icon.png`) — such a case would pass against any handler. This
    is not the loader's guard — that is pinned by
    ``test_load_app_asset_refuses_a_name_that_is_not_a_plain_filename`` below,
    at the seam a name can actually arrive through."""
    client = _client()
    slug = _ships_assets(tmp_path, monkeypatch, {"hero.png": _PNG_1X1})
    (tmp_path / slug / "icon.png").write_bytes(_PNG_1X1)  # beside app.json
    (tmp_path / "secret.png").write_bytes(_PNG_1X1)  # beside the App

    assert client.get(f"/apps/{slug}/assets/hero.png").status_code == 200  # the control
    assert client.get(f"/apps/{slug}/assets/..%2Ficon.png").status_code == 404
    assert client.get(f"/apps/{slug}/assets/..%2F..%2Fsecret.png").status_code == 404


def test_load_app_asset_refuses_a_name_that_is_not_a_plain_filename(tmp_path, monkeypatch):
    """The loader's own rule, at the seam a name arrives through (a manifest
    field for the icon, a decoded path segment for an asset): anything with a
    separator is refused BEFORE a path is built. Every target below really
    exists — the control proves it — so a ``None`` is a refusal, not a miss."""
    from workspace_app.apps.manifest import load_app_asset

    slug = _ships_assets(tmp_path, monkeypatch, {"hero.png": _PNG_1X1})
    (tmp_path / slug / "icon.png").write_bytes(_PNG_1X1)  # beside app.json
    (tmp_path / "secret.png").write_bytes(_PNG_1X1)  # beside the App

    assert load_app_asset(slug, "assets", "hero.png") is not None  # the control
    assert load_app_asset(slug, "assets", "../icon.png") is None
    assert load_app_asset(slug, "assets", "../../secret.png") is None
    assert load_app_asset(slug, "assets", "..\\icon.png") is None
    assert load_app_asset(slug, "assets", "..") is None  # suffix "." — the allowlist refuses it
    assert load_app_asset(slug, "assets", ".") is None
    assert load_app_asset(slug, "", "../secret.png") is None  # the icon seam, same rule
    assert load_app_asset(slug, "", "icon.png") is not None


def test_load_app_asset_treats_a_name_the_filesystem_refuses_as_no_asset(tmp_path, monkeypatch):
    """A name the filesystem itself cannot hold (longer than NAME_MAX, so the
    probe raises ENAMETOOLONG instead of answering "not there") is no asset —
    ``None``, and 404 on the route — not a 500 with a traceback. The assets
    route is the first place a URL segment reaches this loader, so the case
    is reachable by anyone."""
    from workspace_app.apps.manifest import load_app_asset

    client = _client()  # built before the loaders are re-pointed
    slug = _ships_assets(tmp_path, monkeypatch, {"hero.png": _PNG_1X1})
    too_long = "a" * 300 + ".png"

    assert load_app_asset(slug, "assets", "hero.png") is not None  # the control
    assert load_app_asset(slug, "assets", too_long) is None
    assert client.get(f"/apps/{slug}/assets/{too_long}").status_code == 404


def test_get_app_asset_unknown_slug_404():
    assert _client().get("/apps/nope/assets/hero.png").status_code == 404


def test_get_app_asset_serves_only_folders_that_are_apps():
    """The route's own guard: a folder under the apps root that is NOT a
    discovered App (``_template`` — the scaffold, which really ships
    ``assets/example.png``) answers 404 even though the file exists. Without
    the guard the loader would serve it, so this is the input that tells the
    guard apart from a plain file miss (``nope`` above is 404 either way)."""
    from workspace_app.apps.manifest import apps_root

    assert (apps_root() / "_template" / "assets" / "example.png").is_file()  # the control
    assert _client().get("/apps/_template/assets/example.png").status_code == 404


def test_get_app_manifest_onboarding_footer_defaults_empty():
    """`footer` (markdown under the points, above the buttons) is optional: an
    app.json that predates it still loads, and the FE sees `""`."""
    ob = _client().get("/apps/playground").json()["onboarding"]
    assert ob is not None
    assert ob["footer"] == ""
