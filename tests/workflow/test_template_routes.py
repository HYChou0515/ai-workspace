"""#520: browse the shipped workflow TEMPLATES and pull one into an item.

The copy is the whole interaction — after it, the item owns an ordinary
``.workflows/<id>.json`` and the template is never consulted again, so the user can edit
their copy freely and a later platform update can't rewrite it under them.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from specstar import SpecStar

from tests.api._client import TestClient
from workspace_app.api import MessageDelta, RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _app(profile: str = "echo") -> tuple[FastAPI, SpecStar, str]:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([MessageDelta(text="ack"), RunDone()]),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile=profile))
        .resource_id
    )
    return app, spec, item_id


def _base(item_id: str) -> str:
    return f"/a/playground/items/{item_id}"


def test_templates_are_listed_with_what_a_card_needs():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        out = client.get(f"{_base(item_id)}/workflow-templates").json()

    tpl = next(t for t in out if t["id"] == "image-to-knowledge")
    assert tpl["title"] and tpl["description"]
    assert [p["id"] for p in tpl["phases"]] == ["read", "review", "commit"]


def test_incompatible_templates_are_listed_with_the_reason_not_hidden():
    """Hiding a template means nobody learns it exists, or that one profile setting away
    it would work. So an incompatible one still appears — flagged, with the problem
    spelled out. `echo` grants only read_file/write_file, so the vision template can't
    run there."""
    app, _spec, item_id = _app(profile="echo")
    with TestClient(app) as client:
        out = client.get(f"{_base(item_id)}/workflow-templates").json()

    tpl = next(t for t in out if t["id"] == "image-to-knowledge")
    assert tpl["compatible"] is False
    assert any("read_image" in p for p in tpl["problems"]), tpl["problems"]


def test_a_profile_that_grants_the_tools_sees_the_template_as_compatible():
    app, _spec, item_id = _app(profile="dsl")
    with TestClient(app) as client:
        out = client.get(f"{_base(item_id)}/workflow-templates").json()

    tpl = next(t for t in out if t["id"] == "image-to-knowledge")
    assert tpl["compatible"] is True and tpl["problems"] == []


def test_copying_a_template_the_profile_cannot_run_is_refused_with_the_reason():
    """Better a loud refusal now than a workflow that sits in the panel and fails the
    first time someone presses Run."""
    app, _spec, item_id = _app(profile="echo")
    with TestClient(app) as client:
        r = client.post(f"{_base(item_id)}/workflow-templates/image-to-knowledge/copy")
    assert r.status_code == 422
    assert "read_image" in r.json()["detail"]


def test_copy_lands_an_editable_workflow_in_the_item():
    app, _spec, item_id = _app(profile="dsl")
    with TestClient(app) as client:
        r = client.post(f"{_base(item_id)}/workflow-templates/image-to-knowledge/copy")
        assert r.status_code == 200, r.text
        assert r.json()["workflow_id"] == "image-to-knowledge"

        # it is now an ordinary workspace workflow: listed, and readable as a file
        listed = client.get(f"{_base(item_id)}/workflows").json()
        assert "image-to-knowledge" in [w["id"] for w in listed]
        raw = client.get(f"{_base(item_id)}/files/.workflows/image-to-knowledge.json").content
    assert json.loads(raw)["id"] == "image-to-knowledge"


def test_copying_over_an_existing_workflow_is_a_conflict():
    """A second copy must not silently discard the edits the user made to the first —
    the same 409 the file and document routes give for a taken name."""
    app, _spec, item_id = _app(profile="dsl")
    with TestClient(app) as client:
        client.post(f"{_base(item_id)}/workflow-templates/image-to-knowledge/copy")
        client.put(
            f"{_base(item_id)}/files/.workflows/image-to-knowledge.json",
            content=json.dumps({"schema": 1, "id": "image-to-knowledge", "title": "MINE"}),
        )

        r = client.post(f"{_base(item_id)}/workflow-templates/image-to-knowledge/copy")
        assert r.status_code == 409
        kept = client.get(f"{_base(item_id)}/files/.workflows/image-to-knowledge.json").content
    assert json.loads(kept)["title"] == "MINE"  # the user's version survived the refusal


def test_copy_with_overwrite_replaces_the_users_version():
    app, _spec, item_id = _app(profile="dsl")
    with TestClient(app) as client:
        client.post(f"{_base(item_id)}/workflow-templates/image-to-knowledge/copy")
        client.put(
            f"{_base(item_id)}/files/.workflows/image-to-knowledge.json",
            content=json.dumps({"schema": 1, "id": "image-to-knowledge", "title": "MINE"}),
        )

        r = client.post(
            f"{_base(item_id)}/workflow-templates/image-to-knowledge/copy?overwrite=true"
        )
        assert r.status_code == 200, r.text
        raw = client.get(f"{_base(item_id)}/files/.workflows/image-to-knowledge.json").content
    assert json.loads(raw)["title"] != "MINE"  # replaced by the template


def test_copying_an_unknown_template_is_404():
    app, _spec, item_id = _app()
    with TestClient(app) as client:
        r = client.post(f"{_base(item_id)}/workflow-templates/no-such/copy")
    assert r.status_code == 404
