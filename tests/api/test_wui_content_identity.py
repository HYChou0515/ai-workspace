"""The content route acts as the pass's holder, not as the request's cookie.

A deploy reads the current user from the access cookie (`get_spec(settings,
get_user_id=<cookie reader>)`), and an opaque-origin frame sends no cookie on
anything it requests. #904 made the pass the credential, but the specstar
calls inside the route still asked the deploy's reader who was asking — and
with no cookie that reader fails, so every page broke behind a real gateway
while tests (a fixed `default_user`) passed. Built here the way production is
built: one reader, given to the spec, that refuses a request without a cookie.
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

RUNTIME = "/*wui runtime*/"


class NoCookie(Exception):
    """What a cookie reader does with a request that carries none."""


def _app(cookie: dict[str, str | None]):
    def read_cookie() -> str:
        user = cookie["user"]
        if user is None:
            raise NoCookie("no access cookie on this request")
        return user

    # As production wires it: the one reader given to the spec AND to the app,
    # and the workspace files in specstar (whose reads ask the reader too).
    spec = make_spec(default_user=read_cookie)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        get_user_id=read_cookie,
        wui_runtime=lambda: RUNTIME,
    )
    return TestClient(app, raise_server_exceptions=False), spec


def _signed_in_page(cookie: dict[str, str | None]):
    client, spec = _app(cookie)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("alice"):
        iid = rm.create(RcaInvestigation(title="Docs", owner="alice")).resource_id
    page = b"<!doctype html><html><head></head><body>hi</body></html>"
    assert client.put(f"/a/rca/items/{iid}/files/docs/index.html", content=page).status_code == 204
    assert client.put(f"/a/rca/items/{iid}/files/docs/a.css", content=b"b{}").status_code == 204
    r = client.post(f"/a/rca/items/{iid}/wui/pass", json={"folder": "/docs"})
    assert r.status_code == 200, r.text
    return client, r.json()["base"]


def test_a_page_and_its_files_load_with_no_cookie_as_the_pass_holder():
    cookie: dict[str, str | None] = {"user": "alice"}
    client, base = _signed_in_page(cookie)

    cookie["user"] = None  # the frame's requests: no cookie at all
    page = client.get(f"{base}docs/index.html")
    css = client.get(f"{base}docs/a.css")
    ping = client.get(f"{base}__wui/ping")

    assert page.status_code == 200, page.text
    assert "hi" in page.text
    assert css.status_code == 200, css.text
    assert ping.status_code == 200, ping.text


def test_acting_as_the_holder_ends_with_the_content_request():
    """The holder's identity is the content route's alone: the next request
    without a cookie is still nobody."""
    cookie: dict[str, str | None] = {"user": "alice"}
    client, base = _signed_in_page(cookie)

    cookie["user"] = None
    assert client.get(f"{base}docs/index.html").status_code == 200
    other = client.get("/rca-investigation")

    # The deploy's reader is asked again — and finds no cookie.
    assert other.status_code == 500, other.status_code


def test_a_bad_pass_never_acts_as_anyone():
    cookie: dict[str, str | None] = {"user": "alice"}
    client, _base = _signed_in_page(cookie)

    cookie["user"] = None
    r = client.get("/wui-content/not-a-real-token/docs/index.html")

    assert r.status_code == 404
