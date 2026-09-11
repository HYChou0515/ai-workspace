"""The entity routes go through `require_access`, with the verbs their tools need.

Every route in `entity_routes.py` hung off `locator.require_item`, whose own
docstring says "this gate authorizes nobody" — so a caller with no grants could
read, create and rewrite the records of somebody else's PRIVATE item while
`GET /files` on the same item gave them 404. #306 PR3 closed exactly that for
the files / chat / stream routes; #419 added these and reintroduced it. The
first fix had no test at all: reverting it left 63 tests green.

Driven as different users through a mutable `holder["id"]`, like
`test_item_perm.py`.
"""

from __future__ import annotations

from workspace_app.api import create_app
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

_SCHEMA = (
    b"path: issues\n"
    b"fields:\n"
    b"  title: { role: text, required: true }\n"
    b"  status: { role: status, values: [open, done] }\n"
)
_SKELETON = b"---\ntitle: {{arg.title}}\nstatus: open\n---\n\n{{arg.body?}}\n"


def _app(holder: dict[str, str]):
    spec = make_spec(default_user=lambda: holder["id"])
    files = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=files,
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec, files


async def _bobs_item(spec, files, permission: Permission) -> str:
    """A restricted item owned by bob, with an `issue` type and one record whose
    title is the thing a read-denied caller must never be handed."""
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        iid = rm.create(RcaInvestigation(title="t", owner="bob", permission=permission)).resource_id
    await files.write(iid, "/.entity/issue/schema.yaml", _SCHEMA)
    await files.write(iid, "/.entity/issue/skeleton.md", _SKELETON)
    await files.write(
        iid, "/issues/1.md", b"---\ntitle: BOARD SALARY TABLE\nstatus: open\n---\n\nSECRET-BODY\n"
    )
    return iid


def _p(iid: str, suffix: str) -> str:
    return f"/a/rca/items/{iid}{suffix}"


async def test_a_stranger_gets_404_on_every_entity_route():
    holder = {"id": "bob"}
    client, spec, files = _app(holder)
    iid = await _bobs_item(spec, files, Permission(visibility="private"))

    holder["id"] = "mallory"  # no grants at all
    assert client.get(_p(iid, "/entities")).status_code == 404
    assert client.get(_p(iid, "/entity_health")).status_code == 404
    assert client.get(_p(iid, "/entities/issue")).status_code == 404
    assert client.post(_p(iid, "/entities/issue"), json={"args": {"title": "x"}}).status_code == 404
    assert client.put(_p(iid, "/entities/issue/1"), json={"patch": {}}).status_code == 404
    # …and nothing was written behind the 404s.
    assert await files.read(iid, "/issues/1.md") == (
        b"---\ntitle: BOARD SALARY TABLE\nstatus: open\n---\n\nSECRET-BODY\n"
    )


async def test_a_reader_can_read_the_records_but_not_write_them():
    holder = {"id": "bob"}
    client, spec, files = _app(holder)
    iid = await _bobs_item(
        spec,
        files,
        Permission(visibility="restricted", read_meta=["user:carol"], read_content=["user:carol"]),
    )

    holder["id"] = "carol"
    assert client.get(_p(iid, "/entities")).status_code == 200
    listing = client.get(_p(iid, "/entities/issue"))
    assert listing.status_code == 200
    assert listing.json()["entities"][0]["fields"]["title"] == "BOARD SALARY TABLE"
    assert client.post(_p(iid, "/entities/issue"), json={"args": {"title": "x"}}).status_code == 403
    assert client.put(_p(iid, "/entities/issue/1"), json={"patch": {}}).status_code == 403


async def test_a_writer_who_may_not_read_is_not_handed_the_record_back():
    """`PUT` returns the whole updated record — every field plus the markdown
    body, preserved verbatim under an empty patch. Gated on `edit_content` alone
    it returned to a read-denied caller exactly what `GET /entities/issue` had
    just refused them: the same reasoning that put `read_content` on the
    `update_entity` TOOL, applied to the route that does the same thing."""
    holder = {"id": "bob"}
    client, spec, files = _app(holder)
    iid = await _bobs_item(
        spec,
        files,
        Permission(visibility="restricted", read_meta=["user:carol"], edit_content=["user:carol"]),
    )

    holder["id"] = "carol"
    assert client.get(_p(iid, "/entities/issue")).status_code == 403  # the control
    r = client.put(_p(iid, "/entities/issue/1"), json={"patch": {}})
    assert r.status_code == 403
    assert "BOARD SALARY" not in r.text
    assert "SECRET-BODY" not in r.text
    r = client.post(_p(iid, "/entities/issue"), json={"args": {"title": "x"}})
    assert r.status_code == 403


async def test_the_owner_still_does_everything():
    holder = {"id": "bob"}
    client, spec, files = _app(holder)
    iid = await _bobs_item(spec, files, Permission(visibility="private"))

    assert client.get(_p(iid, "/entities/issue")).status_code == 200
    created = client.post(_p(iid, "/entities/issue"), json={"args": {"title": "Login broken"}})
    assert created.status_code == 200, created.text
    assert (
        client.put(_p(iid, "/entities/issue/1"), json={"patch": {"status": "done"}}).status_code
        == 200
    )
