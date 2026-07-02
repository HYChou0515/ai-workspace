"""#380 P3 — the per-turn skill wiring on a real app chat turn:

- the item's ``attached_skill_prefs`` reaches the turn context (so ``read_skill``
  refuses a toggled-off skill live),
- a toggled-off *workspace* skill drops out of the "Skills in this workspace"
  block, and
- ``apply_skills`` on the message preloads the chosen skill bodies into the turn
  (overriding a disabled toggle), one-shot — never persisted into history.

Uses the same create_app + capturing-runner harness as the #298 skill tests.
"""

from __future__ import annotations

from tests.api._client import TestClient
from tests.api.conftest import register_rca_item
from workspace_app.agent import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


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


def _write_skill(client, iid, name, description, body):
    md = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}".encode()
    client.put(f"/a/rca/items/{iid}/files/.skill/{name}/SKILL.md", content=md)


def test_turn_context_carries_the_items_skill_prefs():
    """The item's stored ``attached_skill_prefs`` reaches the turn's
    ``AgentToolContext.skill_prefs`` — the wire that makes read_skill's toggle
    gate fire on a live turn."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec, attached_skill_prefs={"author-skill": False})
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})
    assert cap.ctx is not None
    assert cap.ctx.skill_prefs == {"author-skill": False}


def test_disabled_workspace_skill_is_hidden_from_the_turn_block():
    """A workspace skill toggled OFF (``attached_skill_prefs`` False) drops out of
    the per-turn "Skills in this workspace" block — the agent is never told about a
    skill the user turned off."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec, attached_skill_prefs={"off-skill": False})
    _write_skill(client, iid, "off-skill", "OFFDESC-token", "body")
    _write_skill(client, iid, "on-skill", "ONDESC-token", "body")
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "hi"})
    assert cap.prompt is not None
    assert "ONDESC-token" in cap.prompt  # a live skill is still advertised
    assert "OFFDESC-token" not in cap.prompt  # the disabled one is filtered out


def test_apply_skills_preloads_the_body_into_the_turn():
    """``apply_skills`` on the message hard-preloads the chosen skill's full body
    into the turn (not just its index line) so a small local model applies it
    without having to call read_skill first."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)
    _write_skill(client, iid, "my-skill", "d", "APPLYBODY-token step one")
    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "hi", "apply_skills": ["my-skill"]},
    )
    assert cap.prompt is not None
    assert "APPLYBODY-token step one" in cap.prompt  # the whole body is preloaded


def test_apply_overrides_a_disabled_skill():
    """Applying a skill this turn overrides its OFF toggle — the body is preloaded
    even though ``attached_skill_prefs`` turned it off (apply is a deliberate
    one-shot that beats the persistent disable)."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec, attached_skill_prefs={"my-skill": False})
    _write_skill(client, iid, "my-skill", "d", "OVERRIDE-token")
    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "hi", "apply_skills": ["my-skill"]},
    )
    assert cap.prompt is not None
    assert "OVERRIDE-token" in cap.prompt  # apply beats the disable toggle


def test_apply_skills_reaches_the_turn_context():
    """The message's ``apply_skills`` reach ``AgentToolContext.applied_skills`` so
    read_skill's toggle gate exempts them (apply overrides disable) on a live
    turn — not just in the preloaded prompt block."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)
    _write_skill(client, iid, "my-skill", "d", "body")
    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "hi", "apply_skills": ["my-skill"]},
    )
    assert cap.ctx is not None
    assert cap.ctx.applied_skills == ["my-skill"]


def test_applied_skill_body_is_one_shot_not_carried_into_the_next_turn():
    """The preloaded body is transient: it rides only the applying turn's prompt
    (like the context/workspace blocks) and never enters history, so a later turn
    without ``apply_skills`` doesn't repeat it."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid = register_rca_item(spec)
    _write_skill(client, iid, "my-skill", "d", "ONESHOT-token body")
    client.post(
        f"/a/rca/items/{iid}/messages",
        json={"content": "first", "apply_skills": ["my-skill"]},
    )
    assert cap.prompt is not None
    assert "ONESHOT-token body" in cap.prompt  # preloaded on the applying turn
    client.post(f"/a/rca/items/{iid}/messages", json={"content": "second"})
    assert "ONESHOT-token body" not in cap.prompt  # not carried into the next turn
