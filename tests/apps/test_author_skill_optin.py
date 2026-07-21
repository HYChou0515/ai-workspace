"""#298 P4 — the workspace apps opt into the author-skill meta-skill + save_skill,
and a workspace skill is advertised to the agent each turn.
"""

import pytest

from tests.api._client import TestClient
from workspace_app.agent import AgentToolContext, build_tools
from workspace_app.api import RunDone, create_app
from workspace_app.apps.manifest import load_app_manifest
from workspace_app.apps.skills import merged_profile_skills
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

OPTED_IN = ["rca", "topic-hub", "playground"]


class _Capture:
    def __init__(self) -> None:
        self.prompt: str | None = None
        self.ctx: AgentToolContext | None = None

    async def run(self, prompt, ctx):
        self.prompt = prompt
        self.ctx = ctx
        yield RunDone()


def _app(runner):
    spec = make_spec(default_user="u")
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    return app, spec


@pytest.mark.parametrize("slug", OPTED_IN)
def test_app_opts_into_author_skill_and_save_skill(slug: str):
    manifest = load_app_manifest(slug)
    assert "author-skill" in manifest.agent.skills
    assert "save_skill" in manifest.agent.tools


@pytest.mark.parametrize("slug", OPTED_IN)
def test_app_advertises_author_skill_in_its_skill_index(slug: str):
    manifest = load_app_manifest(slug)
    metas = merged_profile_skills(slug, manifest.default_profile, manifest.agent.skills)
    assert "author-skill" in {m.name for m in metas}


@pytest.mark.parametrize("slug", OPTED_IN)
def test_app_exposes_read_skill_and_save_skill_tools(slug: str):
    manifest = load_app_manifest(slug)
    names = {
        t.name
        for t in build_tools(manifest.agent.tools, app_slug=slug, profile=manifest.default_profile)
    }
    assert "save_skill" in names
    assert "read_skill" in names


def test_skills_endpoint_reports_the_tristate_pref_label():
    """A stored ``attached_skill_prefs`` value surfaces on the picker as its
    ``on`` / ``off`` label (and flips ``effective``) — the twin of the tool
    picker's forced-off row (#380). ``follow`` is the absent-key case the
    across-sources test already covers."""
    from tests.api.conftest import register_rca_item

    app, spec = _app(_Capture())
    client = TestClient(app)
    off = register_rca_item(spec, attached_skill_prefs={"author-skill": False})
    on = register_rca_item(spec, attached_skill_prefs={"author-skill": True})
    off_row = {s["name"]: s for s in client.get(f"/a/rca/items/{off}/skills").json()["skills"]}
    on_row = {s["name"]: s for s in client.get(f"/a/rca/items/{on}/skills").json()["skills"]}
    assert off_row["author-skill"]["pref"] == "off"
    assert off_row["author-skill"]["effective"] is False
    assert on_row["author-skill"]["pref"] == "on"
    assert on_row["author-skill"]["effective"] is True


def test_workspace_skill_is_advertised_to_the_agent_each_turn():
    """A skill saved into the workspace `.skill/` shows up in the next turn's
    prompt (the "Skills in this workspace" block), read live like context_files
    and never persisted (#298 Q3a)."""
    cap = _Capture()
    app, _spec = _app(cap)
    client = TestClient(app)
    iid = client.post("/a/playground/items", json={"title": "scratch"}).json()["resource_id"]
    md = b"---\nname: my-skill\ndescription: SKILLDESC-token\n---\n\nbody"
    client.put(f"/a/playground/items/{iid}/files/.skill/my-skill/SKILL.md", content=md)
    client.post(f"/a/playground/items/{iid}/messages", json={"content": "hello"})
    assert cap.prompt is not None
    assert "Skills in this workspace" in cap.prompt
    assert "my-skill" in cap.prompt
    assert "SKILLDESC-token" in cap.prompt
    assert "hello" in cap.prompt


def test_skills_endpoint_returns_picker_state_across_sources():
    """GET .../skills returns the per-item skills picker state (#380): the App's
    declared shared skills + the workspace's co-created ones, each with its
    source / default_on / pref / effective. A malformed workspace skill is
    skipped (the loader stays lenient)."""
    app, _spec = _app(_Capture())
    client = TestClient(app)
    iid = client.post("/a/playground/items", json={"title": "scratch"}).json()["resource_id"]
    base = f"/a/playground/items/{iid}/files/.skill"
    client.put(f"{base}/alpha/SKILL.md", content=b"---\nname: alpha\ndescription: a\n---\n\nbody")
    client.put(f"{base}/zeta/SKILL.md", content=b"---\nname: zeta\ndescription: z\n---\n\nbody")
    # name/dir mismatch → skipped (the loader is lenient; this never lists)
    client.put(f"{base}/bad/SKILL.md", content=b"---\nname: other\ndescription: x\n---\n\nbody")
    out = client.get(f"/a/playground/items/{iid}/skills").json()
    by = {s["name"]: s for s in out["skills"]}
    assert by["alpha"] == {
        "name": "alpha",
        "description": "a",
        "source": "workspace",
        "default_on": True,
        # A skill written here, not a copy of a baked-in one (#589) — so there is
        # no upstream it could be behind.
        "is_copy": False,
        "update_available": False,
        "pref": "follow",
        "effective": True,
    }
    assert by["zeta"]["source"] == "workspace"
    assert "other" not in by  # malformed → skipped
    # the App's declared shared skill is offered too (default-on in playground)
    assert by["author-skill"]["source"] == "shared"
    assert by["author-skill"]["effective"] is True


def test_skills_endpoint_reports_whether_a_baked_in_skill_has_a_local_copy():
    """#589: once a baked-in skill's files are copied here they are editable,
    downloadable and refreshable — but the row still has to answer as the skill it
    copied, or using a default-off one would quietly turn it on for good. The
    panel therefore needs both facts, so both are on the wire."""
    from tests.api.conftest import register_rca_item

    app, spec = _app(_Capture())
    client = TestClient(app)
    iid = register_rca_item(spec)

    rows = {s["name"]: s for s in client.get(f"/a/rca/items/{iid}/skills").json()["skills"]}

    assert rows["author-skill"]["is_copy"] is False


def test_refreshing_a_skill_requires_write_access_not_just_conversation():
    """#589: materializing a skill's files happens by itself, writing platform
    bytes into a reserved path — so a read-only participant can still use skills.
    Pressing "update" is the opposite: a person deliberately rewriting workspace
    content, possibly over someone else's edits. That needs write access."""
    from workspace_app.apps.rca.model import RcaInvestigation
    from workspace_app.perm import Permission

    app, spec = _app(_Capture())
    client = TestClient(app)
    # Owned by someone else, shared so anyone may enter and talk — but not write.
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("alice"):
        iid = rm.create(
            RcaInvestigation(
                title="t",
                owner="alice",
                permission=Permission(
                    visibility="restricted",
                    read_meta=["all"],
                    read_chat=["all"],
                    converse=["all"],
                ),
            )
        ).resource_id

    r = client.post(f"/a/rca/items/{iid}/skills/author-skill/refresh", json={})

    assert r.status_code == 403


def test_skills_endpoint_reports_when_a_copy_has_an_update_waiting():
    """#589: the refresh control needs a reason to appear. Without this it shows
    on every copy, and pressing it when upstream has not moved does nothing
    visible — which reads exactly like a broken button."""
    from tests.api.conftest import register_rca_item

    app, spec = _app(_Capture())
    client = TestClient(app)
    iid = register_rca_item(spec)

    rows = {s["name"]: s for s in client.get(f"/a/rca/items/{iid}/skills").json()["skills"]}

    assert rows["author-skill"]["update_available"] is False


def test_refresh_endpoint_reports_what_it_updated_and_what_it_left_alone():
    """#589: the result is not a bare OK. "These files were edited here so we did
    not touch them" is the part the user has to act on."""
    from tests.api.conftest import register_rca_item

    app, spec = _app(_Capture())
    client = TestClient(app)
    iid = register_rca_item(spec)

    # Nothing copied here yet, so there is nothing to bring — but the shape of
    # the answer is the contract the panel renders.
    body = client.post(f"/a/rca/items/{iid}/skills/author-skill/refresh", json={}).json()

    assert body == {"updated": [], "skipped": [], "removed": []}
