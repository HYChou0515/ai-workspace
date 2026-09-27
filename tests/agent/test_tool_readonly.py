"""docs/plan-ai-reads-docs.md P1 — what the agent SEES when a tool writes into
a readonly skill's copy: a sentence naming why and what to do instead, not a
traceback the SDK reduces to "an error occurred" (which a model retries).
"""

from pathlib import Path

from agents import RunContextWrapper

import workspace_app.apps.shared_skills as shared
from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import _guard_workspace_full, edit_file_impl, write_file_impl
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.skills import readonly_skill_path, resolve_skill_body
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec

# Applied where tools are BUILT, as the SDK gets them.
write_file = _guard_workspace_full(write_file_impl)
edit_file = _guard_workspace_full(edit_file_impl)


async def _readonly_ctx(tmp_path: Path, monkeypatch):
    sd = tmp_path / "ref"
    (sd / "docs").mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        "---\nname: ref\ndescription: d\nreadonly: true\n---\n\nread docs/\n"
    )
    (sd / "docs" / "a.md").write_text("shipped")
    monkeypatch.setitem(shared.SHARED_SKILLS, "ref", sd)
    spec = make_spec(default_user="bob")
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        iid = rm.create(RcaInvestigation(title="t", owner="bob", permission=None)).resource_id
    files = WorkspaceFiles(MemoryFileStore(), readonly=readonly_skill_path)
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id=iid, files=files, spec=spec, app_slug="rca", acting_user="bob"
        )
    )
    await resolve_skill_body(files, iid, "rca", None, "ref")
    return ctx, files, iid


def _says_readonly(out: str) -> None:
    assert out.startswith("error:")
    assert "readonly" in out
    assert "somewhere else" in out


async def test_a_new_file_in_a_readonly_copy_tells_the_agent_it_is_reference(
    tmp_path: Path, monkeypatch
):
    ctx, files, iid = await _readonly_ctx(tmp_path, monkeypatch)

    _says_readonly(await write_file(ctx, "/.skill/ref/docs/new.md", "mine"))
    assert not await files.exists(iid, "/.skill/ref/docs/new.md")


async def test_an_edit_of_a_readonly_copy_tells_the_agent_it_is_reference(
    tmp_path: Path, monkeypatch
):
    ctx, files, iid = await _readonly_ctx(tmp_path, monkeypatch)

    _says_readonly(await edit_file(ctx, "/.skill/ref/docs/a.md", "shipped", "edited"))
    assert await files.read(iid, "/.skill/ref/docs/a.md") == b"shipped"
