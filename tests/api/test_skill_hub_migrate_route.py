"""The operator's trigger for moving pre-git skill hub entries into git
(docs/plan-skill-hub-history.md §6): superusers only, and a 404 for anyone
else — whether this deploy has such a route is not for a user to probe."""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.skill_hub import SkillHubEntry, SkillHubReview, SkillHubStore
from workspace_app.apps.skill_payload import origin_for
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

ROOT = frozenset({"root"})
_MD = b"---\nname: triage\ndescription: d\n---\nbody\n"


async def test_only_a_superuser_runs_the_migration_and_gets_its_report() -> None:
    holder = {"id": "alice"}
    spec = make_spec(default_user=lambda: holder["id"], superusers=ROOT)
    filestore = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=ROOT,
    )
    await filestore.write("skill-hub:old1:v1", "/SKILL.md", _MD)
    spec.get_resource_manager(SkillHubEntry).create(
        SkillHubEntry(
            owner="alice",
            name="triage",
            description="d",
            source_item="i",
            source_app="rca",
            source_profile="p",
            review=SkillHubReview(verdict="ok"),
            blobs="skill-hub:old1:v1",
            origin=origin_for("hub", {"SKILL.md": _MD}, entry="old1"),
        ),
        resource_id="old1",
    )
    client = TestClient(app)
    hub: SkillHubStore = app.state.skill_hub

    refused = client.post("/admin/skill-hub/migrate")
    assert refused.status_code == 404
    row = hub.get("old1")
    assert row is not None and row.commit == ""

    holder["id"] = "root"
    done = client.post("/admin/skill-hub/migrate")
    assert done.status_code == 200, done.text
    assert done.json() == {"migrated": ["old1"], "duplicates": []}
    row = hub.get("old1")
    assert row is not None and row.commit
