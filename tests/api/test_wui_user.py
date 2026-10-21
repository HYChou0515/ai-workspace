"""A WUI User uses a page without being able to change the item
(docs/plan-wui-user.md)."""

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
    assert "use_wui" in resp.json()["detail"]
