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
    assert base.startswith("/wui-content/") and base.endswith("/")
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
    # The pass is in the URL: a page's own requests must not repeat it to anyone,
    # and nothing served under it may be kept by a cache for the next holder.
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


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


@pytest.mark.parametrize("link", ["docs/site/", "docs/site"])
def test_the_folder_itself_is_the_page_s_own_home(link):
    """A page links home with `../` or `./`. The folder is inside itself — a
    `startswith(folder + "/")` test refused it, and the frame landed on a bare
    error page."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _site(client, iid)
    r0 = client.post(_wp(iid, "/wui/pass"), json={"folder": "/docs/site"})
    base = r0.json()["base"]

    r = client.get(f"{base}{link}", follow_redirects=False)

    if link.endswith("/"):
        assert r.status_code == 200 and "home" in r.text
    else:
        assert (r.status_code, r.headers["location"]) == (301, "site/")


def test_a_page_at_the_workspace_root_opens_at_the_bare_address():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/index.html", b"<html><head></head><body>root home</body></html>")
    base = _pass(client, iid, folder="/")

    r = client.get(base)

    assert r.status_code == 200 and "root home" in r.text


def test_a_directory_redirect_survives_a_name_that_is_not_latin():
    """Header values are latin-1; a raw `說明/` raised and answered 500."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/docs/說明 書/index.html", b"<html><head></head><body>x</body></html>")
    base = _pass(client, iid)

    r = client.get(f"{base}docs/%E8%AA%AA%E6%98%8E%20%E6%9B%B8", follow_redirects=False)

    assert r.status_code == 301
    assert r.headers["location"] == "%E8%AA%AA%E6%98%8E%20%E6%9B%B8/"


def test_the_runtime_goes_after_the_doctype_when_there_is_no_head_tag():
    """HTML lets a page leave `<head>` out. Put before the doctype, the script
    made the browser ignore it — quirks mode, where the single-page way gave
    standards mode."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(
        client,
        iid,
        "/docs/index.html",
        b"\xef\xbb\xbf<!-- hi -->\n<!DOCTYPE html>\n<title>t</title><p>x",
    )
    base = _pass(client, iid)

    text = client.get(f"{base}docs/index.html").text

    assert text.index("<!DOCTYPE html>") < text.index(RUNTIME)


def test_a_missing_page_says_so_in_a_page_that_tells_the_pane():
    """A frame that lands on a page that is gone — a remembered sub-page since
    renamed — must not sit on a bare 'Not found.' the pane never hears about."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    r = client.get(f"{base}docs/site/gone/", headers={"sec-fetch-dest": "iframe"})

    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert RUNTIME in r.text
    assert '<meta name="wui-missing"' in r.text
    # A subresource that is missing stays a plain 404: it is not a page.
    asset = client.get(f"{base}docs/site/gone.css", headers={"sec-fetch-dest": "style"})
    assert asset.status_code == 404 and RUNTIME not in asset.text


def test_a_held_pass_still_lapses_on_time():
    """Looking a pass up is held for a few seconds — a page asks for dozens of
    files at once — but its expiry is judged on every request, held or not."""
    holder = {"id": "bob"}
    clock = {"t": 1_000_000.0}
    client, spec = _client_and_spec(holder, now=lambda: clock["t"])
    iid = _item(spec, by="bob")
    base = _site(client, iid)
    clock["t"] = 1_000_000.0 + PASS_TTL_S - 1
    assert client.get(f"{base}docs/site/index.html").status_code == 200

    clock["t"] += 2

    assert client.get(f"{base}docs/site/index.html").status_code == 404


def test_one_page_load_looks_its_pass_up_once():
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _site(client, iid)
    rm = spec.get_resource_manager(WuiPass)
    calls = []
    real_get = rm.get

    def counting_get(*a, **k):
        calls.append(a)
        return real_get(*a, **k)

    rm.get = counting_get  # type: ignore[method-assign]
    try:
        for _ in range(5):
            assert client.get(f"{base}docs/site/assets/main.css").status_code == 200
    finally:
        rm.get = real_get  # type: ignore[method-assign]

    assert len(calls) <= 1


def test_a_page_cannot_reach_the_rest_of_the_api():
    """A served page may send requests to its own host (`connect-src 'self'`),
    and a request without a session is not necessarily anonymous — the default
    composition answers every request as the configured user. So the API
    refuses what only such a page sends: `Origin: null`, the opaque origin's
    signature on a CORS-mode `fetch`, an XHR and any non-GET request."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    from_page = client.get("/wui", headers={"origin": "null"})
    run = client.post(_wp(iid, "/wui/pass"), json={"folder": "/docs"}, headers={"origin": "null"})

    assert from_page.status_code == 403
    assert run.status_code == 403
    # Its own folder, through its pass, still answers it.
    assert (
        client.get(f"{base}docs/site/assets/main.css", headers={"origin": "null"}).status_code
        == 200
    )
    # And the app itself, which sends a real origin or none, is untouched.
    assert client.get("/wui").status_code == 200
    assert client.get("/wui", headers={"origin": "http://testserver"}).status_code == 200


def test_a_page_s_own_scripts_are_fetched_so_their_errors_can_be_reported():
    """An opaque-origin page's plain `<script src>` is a cross-origin script, and
    the browser MUTES its errors to "Script error." — no message, no file, no line,
    and its promise rejections vanish. The report panel exists for those errors,
    so the page's own scripts are fetched in CORS mode, which the route allows."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    _put(
        client,
        iid,
        "/docs/index.html",
        b'<html><head><script src="./app.js"></script>'
        b'<script type="module" src="m.js" crossorigin="use-credentials"></script>'
        b"<script>inline()</script></head><body></body></html>",
    )
    base = _pass(client, iid)

    text = client.get(f"{base}docs/index.html").text

    assert '<script src="./app.js" crossorigin="anonymous">' in text
    # One the page already marked keeps its own choice; an inline one has nothing to fetch.
    assert '<script type="module" src="m.js" crossorigin="use-credentials">' in text
    assert "<script>inline()</script>" in text


def test_a_page_in_another_encoding_keeps_it():
    """The served way must not turn a non-UTF-8 page's characters into U+FFFD
    by relabelling its bytes UTF-8 — nor its stylesheets' and scripts'."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    big5 = '<html><head><meta charset="big5"></head><body>良率</body></html>'.encode("big5")
    _put(client, iid, "/docs/index.html", big5)
    _put(client, iid, "/docs/s.css", 'p::after{content:"é"}'.encode("latin-1"))
    base = _pass(client, iid)

    page = client.get(f"{base}docs/index.html")
    css = client.get(f"{base}docs/s.css")

    assert "charset=utf-8" not in page.headers["content-type"]
    assert "良率".encode("big5") in page.content and RUNTIME.encode() in page.content
    # No charset: a stylesheet or script without one is read in the encoding of
    # the page that loads it — Big5 for a Big5 page. Labelled latin-1 (round 1)
    # it was right only for a latin-1 file.
    assert css.headers["content-type"] == "text/css"


def test_the_envelope_is_exactly_the_one_documented():
    """Every clause, not the four the other tests name: `form-action 'none'` and
    `base-uri 'self'` are the envelope too."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    csp = client.get(f"{base}docs/site/index.html").headers["content-security-policy"]

    assert csp == (
        "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
        "media-src 'self' data:; connect-src 'self'; worker-src blob:; form-action 'none'; "
        "base-uri 'self'"
    )


def test_a_pass_lives_the_twelve_hours_the_docs_promise():
    assert PASS_TTL_S == 12 * 60 * 60


def test_one_mint_clears_at_most_fifty_lapsed_passes():
    """Bounded so a mint never becomes a sweep of the table (the runbook says 50)."""
    holder = {"id": "bob"}
    clock = {"t": 1_000_000.0}
    client, spec = _client_and_spec(holder, now=lambda: clock["t"])
    iid = _item(spec, by="bob")
    for i in range(55):
        _pass(client, iid, folder=f"/f{i}")
    clock["t"] += PASS_TTL_S + 1

    _pass(client, iid, folder="/fresh")

    rm = spec.get_resource_manager(WuiPass)
    assert len(list(rm.list_resources(QB.all().build()))) == 55 - 50 + 1


def test_marking_scripts_leaves_what_scripts_and_data_say_alone():
    """Only the TAGS are marked. A `<script src=` inside a script's own text, a
    JSON island or a comment is the page's data — rewriting it broke the script
    (a syntax error) and the JSON (a parse error)."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder)
    iid = _item(spec, by="bob")
    page = (
        b'<html><head><!-- <script src="c.js"> -->'
        b"<script>var s = \"<script src='a.js'><\\/script>\";</script>"
        b'<script type="application/json">{"html":"<script src=\\"x.js\\">"}</script>'
        b'<SCRIPT SRC="./up.js"></SCRIPT></head><body></body></html>'
    )
    _put(client, iid, "/docs/index.html", page)
    base = _pass(client, iid)

    text = client.get(f"{base}docs/index.html").text

    assert '<!-- <script src="c.js"> -->' in text
    assert "var s = \"<script src='a.js'><\\/script>\";" in text
    assert '{"html":"<script src=\\"x.js\\">"}' in text
    assert '<SCRIPT SRC="./up.js" crossorigin="anonymous">' in text


def test_a_runtime_with_characters_beyond_ascii_still_goes_into_any_page():
    """The page is worked on as latin-1; a runtime character outside it would
    make the response fail. It is written in as `\\u` escapes instead."""
    holder = {"id": "bob"}
    client, spec = _client_and_spec(holder, runtime='window.x = "\u2026 \U0001f600";')
    iid = _item(spec, by="bob")
    base = _site(client, iid)

    r = client.get(f"{base}docs/site/index.html")

    assert r.status_code == 200
    assert 'window.x = "\\u2026 \\ud83d\\ude00";' in r.text


def test_the_held_passes_are_bounded(monkeypatch):
    """A cache, not a map: it may not grow with every pass ever looked up."""
    from workspace_app.api import wui_content

    monkeypatch.setattr(wui_content, "_HOLD_MAX", 2)
    spec = make_spec(default_user=lambda: "bob")
    wui_content.register_wui_pass(spec)
    passes = wui_content.WuiPasses(spec, now=lambda: 1_000_000.0)
    tokens = [passes.mint(slug="rca", item_id="i", folder=f"/f{n}", user="bob") for n in range(5)]

    for token in tokens:
        assert passes.get(token) is not None
        assert len(passes._held) <= 2
