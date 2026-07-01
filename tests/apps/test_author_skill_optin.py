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
        "pref": "follow",
        "effective": True,
    }
    assert by["zeta"]["source"] == "workspace"
    assert "other" not in by  # malformed → skipped
    # the App's declared shared skill is offered too (default-on in playground)
    assert by["author-skill"]["source"] == "shared"
    assert by["author-skill"]["effective"] is True
