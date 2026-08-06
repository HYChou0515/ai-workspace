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


def test_a_dot_dot_inside_a_name_is_still_allowed():
    """Only path SEGMENTS that are `..` escape; a filename that merely contains
    dots is ordinary and must keep working, or this guard breaks real uploads."""
    client, iid = _client_and_item()

    r = client.put(f"/a/rca/items/{iid}/files/report..final.csv", content=b"ok")

    assert r.status_code == 204, r.text
    assert client.get(f"/a/rca/items/{iid}/files/report..final.csv").content == b"ok"
