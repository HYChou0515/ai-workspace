"""A workspace path may not climb out of the workspace.

`PUT .../files/{path:path}` normalises nothing: Starlette collapses a literal
`../`, so the plain form is already refused, but a **percent-encoded** `..%2F`
arrives intact and is joined straight onto the workspace root. On a warm local /
isolated sandbox the OS then resolves it, so the write lands beside the
workspace in the infra area (next to `.ready` and the per-sandbox `.home`) or,
with enough segments, inside another item's directory on the shared scratch
volume.

This became worth fixing now rather than later because #700 opens these routes
to a cross-origin, cookie-authenticated browser caller for the first time, and
tells that caller to build the path segment out of an external record id whose
format the platform explicitly declines to validate.
"""

from __future__ import annotations

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


def _client_and_item():
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    return TestClient(app), register_rca_item(spec)


@pytest.mark.parametrize(
    "escaping",
    [
        "..%2F..%2Fescaped.csv",  # percent-encoded, survives Starlette's normalisation
        "sub%2F..%2F..%2Fescaped.csv",  # climbs back out after descending
        "a/b/..%2F..%2F..%2Fescaped.csv",  # mixed literal and encoded
    ],
)
def test_an_upload_cannot_write_outside_the_workspace(escaping: str):
    client, iid = _client_and_item()

    r = client.put(f"/a/rca/items/{iid}/files/{escaping}", content=b"PWNED")

    assert r.status_code == 400, f"{escaping} → {r.status_code}"
    listed = {e["path"] for e in client.get(f"/a/rca/items/{iid}/files").json()}
    assert not any(".." in p for p in listed), listed


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_reading_or_deleting_cannot_reach_outside_the_workspace(method: str):
    """The two call sites the first version of this file left uncovered.

    Writing out is the obvious hole, so it got the tests; reading and deleting
    are the ones that matter more. `WorkspaceFiles.read`/`delete` normalise via
    `abs_path`, which does not touch `..`, and hand the path straight to the
    sandbox — so on a warm local sandbox, whose items are sibling directories on
    one shared volume, an unguarded GET is another item's file leaving the
    building and an unguarded DELETE is another item's file gone.

    Measured before this test existed: removing the guard from either route left
    all 46 tests across the file-route suites green. The route guard is the only
    defence, so it needs a test that dies with it.
    """
    client, iid = _client_and_item()
    escaping = "..%2F..%2Fother-item%2Froot%2Fsecret.csv"

    r = client.request(method, f"/a/rca/items/{iid}/files/{escaping}")

    assert r.status_code == 400, f"{method} → {r.status_code}: {r.text}"


def test_a_trailing_slash_url_resolves_to_the_same_stored_file():
    """The URL half of the normalisation fix, which had no test of its own.

    Verified by mutation: putting only the `{path:path}` routes back on `lstrip`
    left every file-route test green, so this half could be reverted silently.
    Before the fix `PUT /files/a.txt/` stored a distinct `/a.txt/` that
    `GET /files/a.txt` then 404'd on — the file existed and was unreachable by
    its own name."""
    client, iid = _client_and_item()

    put = client.put(f"/a/rca/items/{iid}/files/report.md/", content=b"# hi")

    assert put.status_code == 204, put.text
    assert {e["path"] for e in client.get(f"/a/rca/items/{iid}/files").json()} == {"/report.md"}

    canonical = client.get(f"/a/rca/items/{iid}/files/report.md")
    assert canonical.status_code == 200, canonical.text
    assert canonical.content == b"# hi"

    # Fetch it back through the TRAILING-SLASH form too — that is where the
    # normalisation has to hold, and asserting only on the canonical form tests
    # the one spelling that cannot break. The content type has to follow the
    # normalised path as well: `splitext` sees no extension on a string ending in
    # `/`, so guessing from the raw path answers octet-stream and the browser
    # offers a download for a file it should render — with a 200 hiding it.
    via_slash = client.get(f"/a/rca/items/{iid}/files/report.md/")
    assert via_slash.status_code == 200, via_slash.text
    assert via_slash.content == b"# hi"
    assert via_slash.headers["content-type"].startswith("text/markdown"), via_slash.headers


def test_the_json_body_routes_are_guarded_too():
    """The routes that take a path in a JSON BODY, not the URL.

    These are the worse case: Starlette's URL normalisation never runs on a body,
    so here even a LITERAL `../` survives — no percent-encoding needed. Guarding
    only the `{path:path}` routes left `mkdir`, `move` and `copy` writing outside
    the workspace, while the guard's own docstring claimed "no `..` ever" was a
    rule you could read off the code.
    """
    client, iid = _client_and_item()
    assert client.put(f"/a/rca/items/{iid}/files/seed.txt", content=b"hello").status_code == 204

    mkdir = client.post(f"/a/rca/items/{iid}/files/mkdir", json={"path": "../escaped"})
    copy = client.post(
        f"/a/rca/items/{iid}/files/copy", json={"from": "/seed.txt", "to": "../esc.txt"}
    )
    move = client.post(
        f"/a/rca/items/{iid}/files/move", json={"from": "/seed.txt", "to": "../mv.txt"}
    )
    reach_out = client.post(
        f"/a/rca/items/{iid}/files/copy", json={"from": "../../secret.txt", "to": "/stolen.txt"}
    )

    assert [mkdir.status_code, copy.status_code, move.status_code] == [400, 400, 400]
    assert reach_out.status_code == 400, "the SOURCE side must be guarded as well"
    listed = {e["path"] for e in client.get(f"/a/rca/items/{iid}/files").json()}
    assert not any(".." in p for p in listed), listed
    tree = client.get(f"/a/rca/items/{iid}/tree").json()
    assert not any(".." in d for d in tree.get("dirs", [])), tree


def test_a_dot_dot_inside_a_name_is_still_allowed():
    """Only path SEGMENTS that are `..` escape; a filename that merely contains
    dots is ordinary and must keep working, or this guard breaks real uploads."""
    client, iid = _client_and_item()

    r = client.put(f"/a/rca/items/{iid}/files/report..final.csv", content=b"ok")

    assert r.status_code == 204, r.text
    assert client.get(f"/a/rca/items/{iid}/files/report..final.csv").content == b"ok"
