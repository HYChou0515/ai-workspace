"""A WUI User uses a page without being able to change the item
(docs/plan-wui-user.md)."""

import asyncio

import pytest

from tests.api.test_wui_agents import SubRunner, _app, _url
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.perm import Permission

_WUI_USER = "wu"
_READER = "rd"

# Every route a page's bridge reaches for its buttons (plan §3.2), with a body that
# gets past validation — what is asserted is only whether the GATE lets them in.
_PAGE_CALLS = [
    ("post", "build", {"folder": "/page"}),
    ("post", "run", {"workflow": "nope", "inputs": {}}),
    ("post", "tools/lot-status/call", {}),
    ("post", "agents/log-digger/call", {"input": "x"}),
    ("post", "jobs", {"kind": "agent", "name": "log-digger", "input": "x"}),
    ("get", "jobs/whatever", None),
    ("post", "jobs/whatever/stop", None),
]


def _restricted(spec, item_id: str) -> None:
    rm = spec.get_resource_manager(PlaygroundItem)
    item = rm.get(item_id).data
    assert isinstance(item, PlaygroundItem)
    item.permission = Permission(
        visibility="restricted",
        read_meta=[f"user:{_WUI_USER}", f"user:{_READER}"],
        read_content=[f"user:{_READER}"],
        use_wui=[f"user:{_WUI_USER}"],
    )
    with rm.using("bob"):
        rm.update(item_id, item)


def _call(client, item_id: str, method: str, tail: str, body):
    if method == "get":
        return client.get(_url(item_id, tail))
    return (
        client.post(_url(item_id, tail), json=body)
        if body is not None
        else client.post(_url(item_id, tail))
    )


@pytest.mark.parametrize(("method", "tail", "body"), _PAGE_CALLS)
def test_a_wui_user_gets_past_every_page_gate(method: str, tail: str, body):
    """D3: everything a page's buttons do. Whatever the route then answers, it is
    not a refusal for want of a permission."""
    client, item_id, spec = _app(SubRunner(), user=_WUI_USER, creator="bob")
    _restricted(spec, item_id)
    with client:
        resp = _call(client, item_id, method, tail, body)
    # A route may still refuse for its own reasons (no such workflow, a tool the
    # app does not offer pages) — only the permission refusal is asserted away.
    assert not (resp.status_code == 403 and "not authorized" in resp.text), resp.text


@pytest.mark.parametrize(("method", "tail", "body"), _PAGE_CALLS)
def test_a_reader_is_refused_by_name_of_the_permission_they_lack(method: str, tail: str, body):
    """D7 needs to know WHAT was missing to say so: the refusal names use_wui."""
    client, item_id, spec = _app(SubRunner(), user=_READER, creator="bob")
    _restricted(spec, item_id)
    with client:
        resp = _call(client, item_id, method, tail, body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "needs_use_wui"  # D7 reads this code


# ── D12: a page's writes — the workspace's own file routes, a page header ─────

_VIEW = "/tracker/page.ai.yaml"
_VIEW_DOC = b"view: wui\ntitle: Tracker\n"


def _page_app(user: str, *, restricted: bool = True):
    """`test_wui_agents._app`, keeping the store so a test can lay the page down
    as the owner did."""
    from tests.api._client import TestClient
    from workspace_app.api import create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec(default_user=user)
    store = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=store,
        runner=SubRunner(),
        get_user_id=lambda: user,
    )
    rm = spec.get_resource_manager(PlaygroundItem)
    with rm.using("bob"):
        item_id = rm.create(PlaygroundItem(title="t", owner="bob", profile="echo")).resource_id
    if restricted:
        _restricted(spec, item_id)
    asyncio.run(store.write(item_id, _VIEW, _VIEW_DOC))
    return TestClient(app), item_id, store


def _files(item_id: str, path: str) -> str:
    return f"/a/playground/items/{item_id}/files/{path.lstrip('/')}"


@pytest.mark.parametrize(
    ("path", "header", "allowed"),
    [
        ("/tracker/data.json", _VIEW, True),  # inside the page's folder
        ("/tracker/sub/rows.csv", _VIEW, True),  # deeper inside it
        ("/notes.md", _VIEW, False),  # outside the folder
        ("/tracker2/x.json", _VIEW, False),  # a sibling whose name starts the same
        ("/tracker", _VIEW, False),  # the folder itself
        (_VIEW, _VIEW, False),  # the view file: it declares what the page may call
        ("/tracker/data.json", None, False),  # no header: the file tree, an upload
        ("/tracker/data.json", "/tracker/missing.ai.yaml", False),  # no such page
        ("/tracker/data.json", "/tracker/data.json", False),  # not a view file
    ],
)
def test_a_wui_user_writes_only_through_a_page_and_inside_its_folder(path, header, allowed):
    client, item_id, _store = _page_app(_WUI_USER)
    with client:
        headers = {"X-Wui-Page": header} if header else {}
        put = client.put(_files(item_id, path), content=b"{}", headers=headers)
        delete = client.delete(_files(item_id, path), headers=headers)
    if allowed:
        assert put.status_code == 204, put.text
        assert delete.status_code == 204, delete.text
    else:
        assert put.status_code == 403, put.text
        assert delete.status_code == 403, delete.text


def test_a_page_at_the_workspace_root_has_no_folder_to_write_in():
    """The browser already refuses it (`resolveWritePath`): "only its own folder"
    is nothing, not everything, for a page at the root."""
    client, item_id, store = _page_app(_WUI_USER)
    asyncio.run(store.write(item_id, "/root.ai.yaml", _VIEW_DOC))
    with client:
        resp = client.put(
            _files(item_id, "/x.json"), content=b"{}", headers={"X-Wui-Page": "/root.ai.yaml"}
        )
    assert resp.status_code == 403


def test_an_editor_is_unaffected_by_the_header():
    """The union: whoever holds edit_content writes anywhere, header or not."""
    client, item_id, _store = _page_app("bob")
    with client:
        resp = client.put(_files(item_id, "/notes.md"), content=b"x", headers={"X-Wui-Page": _VIEW})
    assert resp.status_code == 204


def test_a_reader_with_the_header_still_cannot_write():
    client, item_id, _store = _page_app(_READER)
    with client:
        resp = client.put(
            _files(item_id, "/tracker/data.json"), content=b"{}", headers={"X-Wui-Page": _VIEW}
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "needs_use_wui"  # D7 reads this code


def test_the_server_answers_the_page_write_table():
    """Parity: the browser's `pageMayWrite` answers the same table
    (`web/tests/wuiPageWriteParity.test.ts`), so the two rules cannot drift."""
    import json
    from pathlib import Path

    from workspace_app.api.wui_page import page_may_write

    table = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "wui_page_write_cases.json").read_text()
    )
    for case in table["cases"]:
        assert page_may_write(case["view"], case["path"]) is case["expected"], case
