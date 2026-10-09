"""A WUI served from a real URL (`docs/plan-wui-multipage.md`, Phase 2).

The page's frame is opaque-origin and — measured in Phase 1 — sends no cookie
on anything it requests, so the content route authorises by a capability token
minted by the (cookie-authenticated) pane. Real `create_app` + a real spec:
the pass is only as good as the access check it repeats, and a double of the
locator would only pretend to repeat it.
"""

from __future__ import annotations

import pytest
from specstar import QB

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.wui_content import PASS_TTL_S, PING_ANSWER, WuiPass
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

RUNTIME = "/*wui runtime*/ window.workspace = {};"


def _client_and_spec(holder: dict[str, str], *, runtime: str | None = RUNTIME, now=None):
    spec = make_spec(default_user=lambda: holder["id"])
    kwargs = {} if now is None else {"wui_now": now}
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        wui_runtime=lambda: runtime,
        **kwargs,
    )
    return TestClient(app), spec


def _item(spec, *, by: str, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(
            RcaInvestigation(title="Docs", owner=by, permission=permission)
        ).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _put(client, iid: str, path: str, body: bytes) -> None:
    assert client.put(_wp(iid, f"/files{path}"), content=body).status_code == 204


def _pass(client, iid: str, folder: str = "/docs") -> str:
    r = client.post(_wp(iid, "/wui/pass"), json={"folder": folder})
    assert r.status_code == 200, r.text
    base = r.json()["base"]
    assert base.startswith("/api/wui-content/") and base.endswith("/")
    return base


def test_a_page_is_served_from_its_pass_with_the_runtime_first_and_the_envelope_as_headers():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(
        client,
        iid,
        "/docs/index.html",
        b"<!doctype html><html><head><title>t</title></head><body>hi</body></html>",
    )

    base = _pass(client, iid)
    r = client.get(f"{base}docs/index.html")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    # The runtime is the head's FIRST child: `window.workspace` must exist when
    # the page's first statement runs, and error capture must be listening
    # before the failure it exists for.
    head = r.text.split("<head>", 1)[1]
    assert head.startswith(f"<script>{RUNTIME}</script>")
    csp = r.headers["content-security-policy"]
    # `sandbox` makes the document opaque even when opened as a top-level page.
    assert csp.startswith("sandbox allow-scripts;")
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "worker-src blob:" in csp
    assert r.headers["access-control-allow-origin"] == "*"


def _site(client, iid: str) -> str:
    _put(client, iid, "/docs/site/index.html", b"<html><head></head><body>home</body></html>")
    _put(
        client, iid, "/docs/site/setup/index.html", b"<html><head></head><body>setup</body></html>"
    )
    _put(client, iid, "/docs/site/assets/main.css", b"body{color:red}")
    return _pass(client, iid)


def test_a_sibling_is_served_as_itself_under_its_own_type():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    r = client.get(f"{base}docs/site/assets/main.css")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")
    assert r.text == "body{color:red}"
    assert r.headers["access-control-allow-origin"] == "*"


def test_a_directory_url_serves_its_index_the_way_a_static_server_does():
    """mkdocs links `setup/` — the directory, not a file."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    r = client.get(f"{base}docs/site/setup/")

    assert r.status_code == 200
    assert "setup" in r.text and r.text.index(RUNTIME) < r.text.index("setup")


def test_a_directory_named_without_its_slash_is_redirected_to_it():
    """Served as-is, `setup` would make the page's own `../assets/x.css` resolve
    one level too high — the reason every static server redirects."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    r = client.get(f"{base}docs/site/setup", follow_redirects=False)

    assert r.status_code == 301
    # Relative, so it holds behind any proxy prefix.
    assert r.headers["location"] == "setup/"


@pytest.mark.parametrize(
    "path",
    [
        "notes.md",  # the item, outside the folder
        "docs2/index.html",  # a sibling whose name starts with the folder's
        "docs/..%2Fnotes.md",  # an encoded climb survives routing intact
    ],
)
def test_a_pass_reads_its_own_folder_and_nothing_beside_it(path):
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/notes.md", b"secret")
    _put(client, iid, "/docs2/index.html", b"<html>other</html>")
    base = _pass(client, iid)

    r = client.get(f"{base}{path}")

    assert r.status_code == 404
    assert "secret" not in r.text and "other" not in r.text


def test_the_frame_needs_no_session_the_pass_carries_whose_authority_it_is():
    """Phase 1: the frame's requests carry no cookie, so the route must not ask
    who is calling — the pass says. Here the 'session' is somebody with no
    access at all."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))
    base = _site(client, iid)

    holder["id"] = ""

    assert client.get(f"{base}docs/site/index.html").status_code == 200


def test_a_pass_stops_working_when_its_user_loses_the_item():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    reader = Permission(
        visibility="restricted", read_meta=["user:alice"], read_content=["user:alice"]
    )
    iid = _item(spec, by="bob", permission=reader)
    _site(client, iid)
    holder["id"] = "alice"
    base = _pass(client, iid)
    assert client.get(f"{base}docs/site/index.html").status_code == 200

    # Through the real route, which tells the locator to forget what it held —
    # so the pass's next request is judged against the new permission.
    holder["id"] = "bob"
    r = client.put(_wp(iid, "/permission"), json={"visibility": "private"})
    assert r.status_code == 200, r.text

    assert client.get(f"{base}docs/site/index.html").status_code in (403, 404)


def test_a_pass_cannot_be_minted_by_someone_who_cannot_read_the_item():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob", permission=Permission(visibility="private"))
    holder["id"] = "mallory"

    r = client.post(_wp(iid, "/wui/pass"), json={"folder": "/docs"})

    assert r.status_code == 404


def test_a_lapsed_pass_reads_nothing_and_says_why():
    holder = {"id": "bob"}
    clock = {"t": 1_000_000.0}
    client, spec = _client_and_spec(holder, now=lambda: clock["t"])
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    clock["t"] += PASS_TTL_S + 1
    r = client.get(f"{base}docs/site/index.html")

    assert r.status_code == 404
    assert "expired" in r.text


def test_an_open_pane_is_handed_the_pass_it_already_holds():
    holder = {"id": "bob"}
    clock = {"t": 1_000_000.0}
    client, spec = _client_and_spec(holder, now=lambda: clock["t"])
    iid = _item(spec, by="bob")

    first = _pass(client, iid)
    clock["t"] += 60
    assert _pass(client, iid) == first
    # …until less than half its life is left: then a fresh one, so a pane never
    # holds a pass about to lapse.
    clock["t"] += PASS_TTL_S // 2
    assert _pass(client, iid) != first


def test_minting_clears_lapsed_passes_so_the_table_does_not_grow_forever():
    holder = {"id": "bob"}
    clock = {"t": 1_000_000.0}
    client, spec = _client_and_spec(holder, now=lambda: clock["t"])
    iid = _item(spec, by="bob")
    _pass(client, iid, folder="/a")
    clock["t"] += PASS_TTL_S + 1

    _pass(client, iid, folder="/b")

    rm = spec.get_resource_manager(WuiPass)
    assert [r.data.folder for r in rm.list_resources(QB.all().build())] == ["/b"]


def test_the_ping_says_the_route_can_serve_a_page():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _pass(client, iid)

    r = client.get(f"{base}__wui/ping")

    assert (r.status_code, r.text) == (200, PING_ANSWER)
    assert r.headers["access-control-allow-origin"] == "*"


def test_without_a_runtime_no_page_is_served_and_the_ping_says_so():
    """A page without the runtime has no bridge and no error capture — the pane
    must fall back rather than show it."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder, runtime=None)
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    assert client.get(f"{base}__wui/ping").status_code == 503
    assert client.get(f"{base}docs/site/index.html").status_code == 503
    # Not a page: served without one.
    assert client.get(f"{base}docs/site/assets/main.css").status_code == 200
