"""The FE edits an investigation's metadata via specstar's auto JSON-Patch
route (PATCH /investigation/{id}) — no custom endpoint. Guard that it works."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.sandbox.mock import MockSandbox


def _client() -> TestClient:
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    return TestClient(app)


def test_patch_investigation_edits_metadata():
    client = _client()
    rid = client.post(
        "/investigation", json={"title": "Old", "owner": "u", "topics": ["a"]}
    ).json()["resource_id"]

    r = client.patch(
        f"/investigation/{rid}",
        json=[
            {"op": "replace", "path": "/title", "value": "New title"},
            {"op": "replace", "path": "/description", "value": "revised"},
            {"op": "replace", "path": "/severity", "value": "P0"},
            {"op": "replace", "path": "/product", "value": "MX-7"},
            {"op": "replace", "path": "/topics", "value": ["x", "y"]},
        ],
    )
    assert r.status_code == 200

    data = client.get(f"/investigation/{rid}").json()
    data = data.get("data", data)  # specstar entry wraps the struct in `data`
    assert data["title"] == "New title"
    assert data["topics"] == ["x", "y"]
    assert data["severity"] == "P0"
    assert data["product"] == "MX-7"
