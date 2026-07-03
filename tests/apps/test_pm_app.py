"""The Project-Management App (#419) ships a coherent declarative bundle: a thin
``PmProject`` item plus two file-first entity types (issue + milestone) whose
schemas, skeletons, and views seed into every new project. This drives the
*shipped* bundle end-to-end through the real create/query entity routes, so the
app.json + schema.yaml + skeleton.md + view files stay coherent together.
"""

from __future__ import annotations

from tests.api._client import TestClient as ApiTestClient
from workspace_app.api import RunDone, ScriptedAgentRunner, create_app
from workspace_app.apps.catalog import validate_function_coherence
from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.pm.model import MODEL, PmProject, Status
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _client() -> ApiTestClient:
    spec = make_spec(default_user="u")
    filestore = SpecstarFileStore(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([RunDone()]),
    )
    return ApiTestClient(app)


def test_pm_manifest_is_coherent_and_ide_first():
    """The shipped app.json passes the startup function-coherence gate and opens
    the workspace up front (its views are the main stage)."""
    m = load_app_manifest("pm")
    validate_function_coherence(m)  # raises if tools ↔ toggles disagree
    assert m.slug == "pm"
    assert m.layout.primary_surface == "ide"
    assert m.item.noun == "Project"


def test_pm_model_is_a_thin_status_only_item():
    """The item carries only its lifecycle status — the real structure lives in
    file-first entities, not in more typed item columns (#419)."""
    assert MODEL is PmProject
    assert PmProject(title="t", owner="u").status is Status.ACTIVE


def test_new_project_seeds_both_entity_types_with_quick_create_forms():
    c = _client()
    iid = c.post("/a/pm/items", json={"title": "Launch"}).json()["resource_id"]

    catalog = c.get(f"/a/pm/items/{iid}/entities").json()
    by_name = {t["name"]: t for t in catalog["types"]}
    assert set(by_name) == {"issue", "milestone"}
    # the issue quick-create form is derived from the skeleton's {{arg}} slots
    issue_form = {f["name"] for f in by_name["issue"]["form"]}
    assert {"title", "assignee", "due", "milestone", "body"} <= issue_form


def test_issue_and_milestone_create_number_from_one_and_roll_up():
    c = _client()
    iid = c.post("/a/pm/items", json={"title": "Launch"}).json()["resource_id"]
    base = f"/a/pm/items/{iid}"

    m1 = c.post(f"{base}/entities/milestone", json={"args": {"title": "Beta"}}).json()
    assert m1["number"] == 1

    i1 = c.post(
        f"{base}/entities/issue",
        json={"args": {"title": "Login broken", "milestone": "1"}},
    ).json()
    assert i1["number"] == 1
    assert i1["fields"]["status"] == "open"
    assert i1["fields"]["milestone"] == 1

    # bump the issue's progress; the milestone's avg-progress rollup + open_count
    # + issues back-reference all recompute on read (P2 projection).
    c.put(f"{base}/entities/issue/1", json={"patch": {"progress": 40}})
    milestones = c.get(f"{base}/entities/milestone").json()["entities"]
    beta = next(e for e in milestones if e["number"] == 1)
    assert beta["fields"]["issues"] == [1]
    assert beta["fields"]["progress"] == 40
    assert beta["fields"]["open_count"] == 1


def test_date_and_daterange_fields_serialize_as_strings_for_the_frontend():
    """YAML auto-parses `due: 2026-02-01` into a Python date; it must survive the
    JSON response as a plain ISO string (not 500 the endpoint), and a daterange
    stays the `start/end` string the gantt view parses."""
    c = _client()
    iid = c.post("/a/pm/items", json={"title": "Launch"}).json()["resource_id"]
    created = c.post(
        f"/a/pm/items/{iid}/entities/issue",
        json={"args": {"title": "Ship", "due": "2026-02-01", "span": "2026-01-01/2026-02-01"}},
    ).json()
    assert created["fields"]["due"] == "2026-02-01"
    assert created["fields"]["span"] == "2026-01-01/2026-02-01"
