"""docs/plan-ai-reads-docs.md P1 — the app's own facade refuses writes into a
readonly skill's copy.

The guard is only a guard once the facade the app BUILDS carries it: these go
through the real `create_app` and its file route, not a facade a test made.
"""

from __future__ import annotations

from pathlib import Path

import workspace_app.apps.shared_skills as shared
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.skills import materialize_skill
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient as ApiTestClient
from .conftest import Harness, register_rca_item


def _readonly_skill(root: Path) -> Path:
    sd = root / "ref"
    (sd / "docs").mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: ref\ndescription: d\nreadonly: true\n---\n\nread docs/\n"
    )
    (sd / "docs" / "a.md").write_text("shipped")
    return sd


def test_the_file_route_refuses_a_write_into_a_readonly_copy(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(shared.SHARED_SKILLS, "ref", _readonly_skill(tmp_path))
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    iid = register_rca_item(spec)
    client = ApiTestClient(app)

    refused = client.put(f"/a/rca/items/{iid}/files/.skill/ref/docs/a.md", content=b"edited")

    assert refused.status_code == 403
    assert refused.json()["error"] == "readonly_skill"
    assert client.get(f"/a/rca/items/{iid}/files/.skill/ref/docs/a.md").status_code == 404
    # the control: the same route still writes an ordinary file
    assert client.put(f"/a/rca/items/{iid}/files/notes.md", content=b"mine").status_code == 204


async def test_workspace_search_and_replace_leave_a_readonly_copy_out(tmp_path: Path, monkeypatch):
    """Review #865 round 1 (regression A1): the workspace-wide search listed the
    readonly copy too -- a common word drew hundreds of docs hits -- and Replace
    wrote into it first (`.skill/` sorts early): a 403, and nothing replaced,
    not even the person's own files. The copy is reference, not their content."""
    sd = _readonly_skill(tmp_path)
    (sd / "docs" / "a.md").write_text("the shipped workspace words")
    monkeypatch.setitem(shared.SHARED_SKILLS, "ref", sd)
    spec = make_spec()
    store = SpecstarFileStore(spec)
    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=store, runner=ScriptedAgentRunner([])
    )
    iid = register_rca_item(spec)
    client = ApiTestClient(app)
    await materialize_skill(WorkspaceFiles(store), iid, "rca", None, "ref")
    assert (
        client.put(f"/a/rca/items/{iid}/files/notes.md", content=b"my workspace notes").status_code
        == 204
    )

    hits = client.post(f"/a/rca/items/{iid}/search", json={"query": "workspace"}).json()
    done = client.post(
        f"/a/rca/items/{iid}/replace", json={"query": "workspace", "replacement": "item"}
    )

    assert [h["path"] for h in hits] == ["/notes.md"]
    assert done.status_code == 200, done.text
    assert client.get(f"/a/rca/items/{iid}/files/notes.md").content == b"my item notes"
    copy = client.get(f"/a/rca/items/{iid}/files/.skill/ref/docs/a.md").content
    assert copy == b"the shipped workspace words"


async def test_the_skills_panel_says_which_copy_is_readonly(
    harness: Harness, tmp_path: Path, monkeypatch
):
    monkeypatch.setitem(shared.SHARED_SKILLS, "ref", _readonly_skill(tmp_path))
    tool = tmp_path / "tool"
    (tool / "scripts").mkdir(parents=True)
    (tool / "SKILL.md").write_text("---\nname: tool\ndescription: d\n---\n\nrun scripts/x.py\n")
    (tool / "scripts" / "x.py").write_text("print(1)")
    monkeypatch.setitem(shared.SHARED_SKILLS, "tool", tool)
    # the copies exactly as a read_skill writes them
    files = WorkspaceFiles(harness.filestore)
    for name in ("ref", "tool"):
        await materialize_skill(files, harness.iid, "rca", None, name)

    rows = {r["name"]: r for r in harness.client.get(harness.wpath("/skills")).json()["skills"]}

    assert rows["ref"]["readonly"] is True
    assert rows["tool"]["readonly"] is False
