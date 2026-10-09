"""`GET /a/{slug}/items/{id}/files/preview?path=` (docs/plan-pptx-preview.md
N1–N6, D3–D5): a slide deck as a PDF, converted in the item's sandbox and
cached beside it. A big deck is converted only once the person says yes; one
already converted is served without asking.
"""

from __future__ import annotations

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app, slide_preview
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item

DECK = b"PK\x03\x04 a slide deck"


def _client_and_item(sandbox: MockSandbox | None = None):  # noqa: ANN202
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=sandbox or MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    client, iid = TestClient(app), register_rca_item(spec)
    assert client.put(f"/a/rca/items/{iid}/files/slides/q3.pptx", content=DECK).status_code == 204
    return client, iid


def _preview(client, iid, path="slides/q3.pptx", **params):  # noqa: ANN001, ANN202
    return client.get(f"/a/rca/items/{iid}/files/preview", params={"path": path, **params})


def test_a_deck_comes_back_as_a_pdf():
    client, iid = _client_and_item()

    r = _preview(client, iid)

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_a_big_deck_is_converted_only_after_the_person_says_yes(monkeypatch):
    """N6: the limit is the server's; the page asks with what it says."""
    monkeypatch.setattr(slide_preview, "CONFIRM_BYTES", 10)
    client, iid = _client_and_item()

    asked = _preview(client, iid)
    yes = _preview(client, iid, confirm="1")

    assert asked.status_code == 409, asked.text
    assert asked.json()["detail"] == {
        "code": "preview_needs_confirm",
        "size": len(DECK),
        "limit": 10,
    }
    assert yes.status_code == 200 and yes.content.startswith(b"%PDF")


def test_a_big_deck_already_converted_is_served_without_asking(monkeypatch):
    client, iid = _client_and_item()
    assert _preview(client, iid).status_code == 200
    monkeypatch.setattr(slide_preview, "CONFIRM_BYTES", 10)

    r = _preview(client, iid)

    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_a_failed_conversion_says_why():
    sandbox = MockSandbox()
    sandbox.fail_preview = "source file could not be loaded"
    client, iid = _client_and_item(sandbox)

    r = _preview(client, iid)

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {
        "code": "preview_failed",
        "why": "source file could not be loaded",
    }


def test_a_missing_deck_is_404():
    client, iid = _client_and_item()

    assert _preview(client, iid, path="slides/nope.pptx").status_code == 404


@pytest.mark.parametrize("path", ["notes.md", "slides/q3.pptx.bak", "deck"])
def test_only_a_slide_deck_is_previewed(path: str):
    """N4: pptx, ppt, odp."""
    client, iid = _client_and_item()

    assert _preview(client, iid, path=path).status_code == 415


@pytest.mark.parametrize("name", ["q3.PPT", "q3.odp"])
def test_every_slide_format_is_previewed(name: str):
    client, iid = _client_and_item()
    client.put(f"/a/rca/items/{iid}/files/slides/{name}", content=DECK)

    assert _preview(client, iid, path=f"slides/{name}").status_code == 200


def test_a_path_that_climbs_out_of_the_workspace_is_refused():
    """The same rule as every other file route (`_workspace_path`)."""
    client, iid = _client_and_item()

    assert _preview(client, iid, path="../other/q3.pptx").status_code == 400


def test_a_leading_slash_names_the_same_deck():
    client, iid = _client_and_item()

    assert _preview(client, iid, path="/slides/q3.pptx").status_code == 200


# ── who may preview (N5) ─────────────────────────────────────────────────────


async def test_a_reader_may_preview_and_a_stranger_may_not():
    """N5: reading the deck is enough — the converter is the platform's."""
    from workspace_app.apps.rca.model import RcaInvestigation
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.perm import Permission

    holder = {"id": "bob"}
    spec = make_spec(default_user=lambda: holder["id"])
    files = MemoryFileStore()
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=files,
            runner=ScriptedAgentRunner([]),
            get_user_id=lambda: holder["id"],
        )
    )
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        iid = rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(
                    visibility="restricted",
                    read_meta=["user:carol", "user:dan"],
                    read_content=["user:carol"],
                ),
            )
        ).resource_id
    await files.write(iid, "/q3.pptx", DECK)

    holder["id"] = "carol"  # may read the content
    assert _preview(client, iid, path="q3.pptx").status_code == 200
    holder["id"] = "dan"  # may see the item, not its files
    assert _preview(client, iid, path="q3.pptx").status_code == 403
    holder["id"] = "mallory"  # no grants at all
    assert _preview(client, iid, path="q3.pptx").status_code == 404
