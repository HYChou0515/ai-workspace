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


def test_pm_manifest_is_coherent_and_views_first():
    """The shipped app.json passes the startup function-coherence gate and opens
    its declarative views as the main stage (§B5)."""
    m = load_app_manifest("pm")
    validate_function_coherence(m)  # raises if tools ↔ toggles disagree
    assert m.slug == "pm"
    assert m.layout.primary_surface == "views"
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


def test_a_new_issue_is_schedulable_out_of_the_box():
    """The scheduler needs three things from an issue that the schema had no way
    to say: how long the work takes, whether those days are working days, and
    whether the system is allowed to move it. A new issue is `auto` from birth —
    otherwise every issue has to be opted in by hand before the Timeline can lay
    anything out at all."""
    c = _client()
    item = c.post("/api/a/pm/items", json={"title": "P"}).json()["resource_id"]
    made = c.post(
        f"/api/a/pm/items/{item}/entities/issue",
        json={"args": {"title": "Cut the release", "exp_days": 3, "exp_days_unit": "working"}},
    ).json()
    assert made["fields"]["exp_days"] == 3
    assert made["fields"]["exp_days_unit"] == "working"
    assert made["fields"]["schedule"] == "auto"


def test_a_new_milestone_is_auto_too_so_its_span_follows_its_issues():
    c = _client()
    item = c.post("/api/a/pm/items", json={"title": "P"}).json()["resource_id"]
    made = c.post(
        f"/api/a/pm/items/{item}/entities/milestone", json={"args": {"title": "M1"}}
    ).json()
    assert made["fields"]["schedule"] == "auto"


def test_a_milestone_may_state_only_when_it_starts():
    """Its start is the lower bound for its issues; its end is what the schedule
    works out. A range that must be filled on both ends cannot say that."""
    c = _client()
    item = c.post("/api/a/pm/items", json={"title": "P"}).json()["resource_id"]
    made = c.post(
        f"/api/a/pm/items/{item}/entities/milestone",
        json={"args": {"title": "M1", "span": "2026-07-01/"}},
    ).json()
    assert made["fields"]["span"] == "2026-07-01/"


def test_an_issue_can_carry_how_urgent_it_is_but_need_not():
    """Urgency exists to be SEEN — the Gantt colours bars by it — so it has to
    be a select with pinned colours rather than free text, and the order of
    the values has to be the order of the urgency so it sorts and so the
    palette can run hot to cold.

    Optional on purpose. Most issues are ordinary, and a required field would
    make every one of them a decision about how much of an emergency it is
    not."""
    c = _client()
    item = c.post("/api/a/pm/items", json={"title": "P"}).json()["resource_id"]

    plain = c.post(
        f"/api/a/pm/items/{item}/entities/issue", json={"args": {"title": "Ordinary"}}
    ).json()
    urgent = c.post(
        f"/api/a/pm/items/{item}/entities/issue",
        json={"args": {"title": "Line down", "urgency": "critical"}},
    ).json()

    assert plain["fields"].get("urgency") in (None, "")
    assert urgent["fields"]["urgency"] == "critical"


def test_the_urgency_scale_runs_hot_to_cold_with_pinned_colours():
    """Hash-assigned colours would put `low` on red as readily as `critical`.
    A scale only reads as a scale if the palette agrees with it."""
    import yaml

    from workspace_app.apps.profiles import _profiles_root

    schema = yaml.safe_load(
        (_profiles_root("pm") / "default" / ".entity" / "issue" / "schema.yaml").read_text("utf-8")
    )
    urgency = schema["fields"]["urgency"]

    assert urgency["values"] == ["critical", "high", "medium", "low"]
    assert urgency["colors"] == {
        "critical": "red",
        "high": "amber",
        "medium": "blue",
        "low": "slate",
    }


def _gantt_views() -> dict[str, dict]:
    import yaml

    from workspace_app.apps.profiles import _profiles_root

    views = _profiles_root("pm") / "default" / "views"
    loaded = {p.stem: yaml.safe_load(p.read_text("utf-8")) for p in views.glob("*.ai.yaml")}
    return {name: spec for name, spec in loaded.items() if spec.get("view") == "gantt"}


def test_every_gantt_view_reads_the_same_calendar():
    """Timeline and Workload showed the same issues on different time axes,
    because each view file started from a blank sheet and only one of them had
    the week numbering and the working-day collapse.

    The axis is the calendar, not the entity — a date range that is one width
    on one tab and another width on the next is the complaint. So this holds
    across every gantt view, including the roadmap over milestones."""
    axis = {
        name: {k: spec.get(k) for k in ("week", "skip_weekends")}
        for name, spec in _gantt_views().items()
    }
    first = next(iter(axis.values()))
    for name, got in axis.items():
        assert got == first, f"{name} draws a different time axis from the other gantt views"


def test_gantt_views_over_one_entity_differ_only_by_grouping():
    """Where they chart the same records, the only thing a second view is FOR
    is asking a different question of them. Anything else that differs is
    drift — which is what Workload was.

    Scoped to one entity because `schedule` and `assignee` name issue fields,
    and the roadmap charts milestones, which have neither."""
    for entity in {spec["entity"] for spec in _gantt_views().values()}:
        same = {
            name: {k: v for k, v in spec.items() if k not in {"title", "group_by"}}
            for name, spec in _gantt_views().items()
            if spec["entity"] == entity
        }
        first = next(iter(same.values()))
        for name, got in same.items():
            assert got == first, (
                f"{name} differs from the other {entity} gantts by more than its grouping"
            )


def test_the_check_covers_whatever_gantt_views_exist():
    """The guard has to outlive these two files. It reads the profile rather
    than naming Timeline and Workload, so a third gantt view added tomorrow is
    under the rule the moment it lands — and if it forgets the axis, this goes
    red instead of shipping a third way for the same issues to look.

    Deliberately a check rather than an inheritance mechanism. Two files did
    not justify inventing one, the seeder is app-agnostic and PM's week
    numbering is not, and a view file that says everything is a view file the
    gear panel can keep editing in place."""
    from workspace_app.apps.profiles import _profiles_root

    on_disk = {p.stem for p in (_profiles_root("pm") / "default" / "views").glob("*.ai.yaml")}

    assert set(_gantt_views()) <= on_disk
    assert "gantt.ai" in _gantt_views() and "workload.ai" in _gantt_views()
