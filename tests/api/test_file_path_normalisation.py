"""A trailing slash must not change what a file operation means.

Regression guard. The `..` containment check was added by REPLACING the three
JSON-body routes' own normalisation, and the replacement was not equivalent:
they had used `strip("/")`, the shared helper used `lstrip("/")`. Trailing
slashes survived, and `"/folder/"` stopped being the same path as `"/folder"`.

The worst case was silent data loss with no error: moving a file onto an
existing directory used to be refused (409); afterwards it succeeded, the source
file was gone, and the workspace held a FILE whose stored path was `/folder/`
sitting beside the DIRECTORY `/folder`. The file tree splits on "/" and drops
empty segments, so both render as the same name at the same level.

Reachable from the shipped UI, not just an API client: `FileTree.commitRename`
collapses runs of slashes but does not strip a trailing one, so renaming to
`foo/` takes this path.

**No migration was needed.** Rows stored under a trailing-slash key would have
been left behind by the window this was live, and they would be worse than
cosmetic: the canonical form now resolves to the sibling file, so a `DELETE` of
the trailing-slash path destroys the good file and keeps the phantom, and a
phantom with no sibling is unaddressable by any API call while still counting
toward the workspace quota. The operator checked the durable store — **zero rows
end in `/`** — so nothing exists to migrate. That is a point-in-time answer about
the environment queried, not a property the schema enforces; if a deployment
turns up that ran the affected build for longer, count again before assuming.
"""

from __future__ import annotations

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


def _paths(client: TestClient, iid: str) -> set[str]:
    return {e["path"] for e in client.get(f"/a/rca/items/{iid}/files").json()}


def test_moving_a_file_onto_an_existing_directory_is_still_refused():
    """The data-loss case. `to` naming a directory must stay a conflict — not
    become a successful move to a path that merely looks like that directory."""
    client, iid = _client_and_item()
    client.put(f"/a/rca/items/{iid}/files/seed.txt", content=b"precious")
    client.post(f"/a/rca/items/{iid}/files/mkdir", json={"path": "/folder"})

    r = client.post(f"/a/rca/items/{iid}/files/move", json={"from": "/seed.txt", "to": "/folder/"})

    assert r.status_code == 409, r.text
    assert "/seed.txt" in _paths(client, iid), "the source file must survive a refused move"


def test_a_trailing_slash_names_the_same_file():
    """`/moved.txt/` and `/moved.txt` must not become two different files."""
    client, iid = _client_and_item()
    client.put(f"/a/rca/items/{iid}/files/seed.txt", content=b"x")

    r = client.post(
        f"/a/rca/items/{iid}/files/move", json={"from": "/seed.txt", "to": "/moved.txt/"}
    )

    assert r.status_code == 204, r.text
    assert "/moved.txt" in _paths(client, iid)


def test_mkdir_treats_a_trailing_slash_as_the_same_directory():
    """`/notes` and `/notes/` name one directory, so making it twice leaves ONE.

    `mkdir` is idempotent by design (it only 409s on `FileExists`), so the status
    code is not the tell — the count is. Without the trailing-slash
    normalisation this produced two entries, `/notes` and `/notes/`, which the
    file tree renders as two folders with the same name at the same level."""
    client, iid = _client_and_item()

    first = client.post(f"/a/rca/items/{iid}/files/mkdir", json={"path": "/notes"})
    second = client.post(f"/a/rca/items/{iid}/files/mkdir", json={"path": "/notes/"})

    assert (first.status_code, second.status_code) == (204, 204), (first.text, second.text)
    dirs = client.get(f"/a/rca/items/{iid}/tree").json()["dirs"]
    assert [d for d in dirs if "notes" in d] == ["/notes"], dirs


def test_a_directory_can_still_be_moved_by_its_trailing_slash_form():
    """Callers write `{"from": "/d/"}` to mean the directory; that used to work
    and must keep working — the guard broke it into a 404."""
    client, iid = _client_and_item()
    client.put(f"/a/rca/items/{iid}/files/d/inner.txt", content=b"x")

    r = client.post(f"/a/rca/items/{iid}/files/move", json={"from": "/d/", "to": "/e/"})

    assert r.status_code == 204, r.text
    assert "/e/inner.txt" in _paths(client, iid)
