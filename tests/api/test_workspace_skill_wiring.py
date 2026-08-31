"""Whether this turn can load a skill decides whether `read_skill` is built.

The index that advertises skills and the tool list that must contain the tool to
load them were computed by two layers that never compared notes. The turn
context now answers the question once, from the same `effective_item_skills`
resolver the picker and the prompt index use, and carries the answer. Driven
through `create_app`, so the wiring under test is the real one.
"""

from __future__ import annotations

from typing import Any

import workspace_app.api.app as app_mod
from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.apps.pm.model import PmProject
from workspace_app.apps.skills import effective_item_skills
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import AgentConfig, make_spec
from workspace_app.sandbox.mock import MockSandbox

_SKILL = "---\nname: bug-report\ndescription: How we write bug reports.\n---\n\nBody.\n"

# playground/echo ships no package `.skill/`; its reachable skills are the three
# shared ones the App declares. Pinning all three OFF leaves the App side with
# nothing, which is what puts the workspace source on the critical path.
_ALL_APP_SKILLS_OFF = {"author-skill": False, "author-workflow": False, "grill-me": False}


async def _dummy_subagent(*_a, **_k):
    return "", []


def _build(monkeypatch, prefs: dict[str, bool] | None = None, *, pm: bool = False):
    spec = make_spec()
    filestore = SpecstarFileStore(spec)
    captured: dict[str, Any] = {}
    real = app_mod.TurnContextBuilder

    def _capture(**kw):
        b = real(**kw)
        captured["builder"] = b
        return b

    monkeypatch.setattr(app_mod, "TurnContextBuilder", _capture)
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=filestore,
        runner=ScriptedAgentRunner([RunDone()]),
    )
    # pm/default opts into two of its App's three declared shared skills, so it is
    # the shipped combination that has a default-OFF skill to reason about;
    # playground/echo has all three on.
    model, profile = (PmProject, "default") if pm else (PlaygroundItem, "echo")
    item_id = (
        spec.get_resource_manager(model)
        .create(model(title="t", owner="u", profile=profile, attached_skill_prefs=prefs or {}))
        .resource_id
    )
    builder = captured["builder"]
    # The SLUG is what selects the app.json + profile dir this test reasons
    # about; both candidate Apps use the profile name "default", so asserting
    # the profile cannot tell them apart — and a fixture that quietly builds
    # the other App's item leaves every test green and testing nothing.
    assert builder._locator.slug_of(item_id) == ("pm" if pm else "playground")
    return filestore, builder, item_id


async def _turn(builder, item_id, agent_config=None):
    return await builder.build_chat_turn(
        item_id,
        agent_config=agent_config,
        run_subagent=_dummy_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )


async def test_a_skill_saved_in_the_workspace_makes_the_turn_reach_one(monkeypatch):
    """The App side is pinned off, so only the workspace `.skill/` can answer —
    the source the grant used to be blind to."""
    filestore, builder, item_id = _build(monkeypatch, _ALL_APP_SKILLS_OFF)
    await filestore.write(item_id, "/.skill/bug-report/SKILL.md", _SKILL.encode())

    ctx = await _turn(builder, item_id)

    assert ctx.skills_reachable is True


async def test_a_turn_whose_every_skill_is_pinned_off_reaches_none(monkeypatch):
    """Nothing loadable from any source: the tool would refuse every call, and a
    refusal reads to a model as "stop trying" (#537). The prompt advertises
    nothing either, so tool and index agree."""
    _filestore, builder, item_id = _build(monkeypatch, _ALL_APP_SKILLS_OFF)

    ctx = await _turn(builder, item_id)

    assert ctx.skills_reachable is False


async def test_the_app_own_skills_answer_without_reading_the_workspace(monkeypatch):
    """With the App's own skills effective the answer cannot change, so the turn
    must not pay for a live workspace listing to confirm it — the send path
    already reads `.skill/` once to render the index."""
    import workspace_app.api.turn_context as tc

    _filestore, builder, item_id = _build(monkeypatch)
    calls: list[str] = []
    real = tc.advertised_workspace_skills

    async def _counting(*a, **kw):
        calls.append("read")
        return await real(*a, **kw)

    monkeypatch.setattr(tc, "advertised_workspace_skills", _counting)

    ctx = await _turn(builder, item_id)

    assert ctx.skills_reachable is True
    assert calls == []


async def test_an_unreadable_workspace_leaves_the_answer_unknown(monkeypatch):
    """A skill index is a capability; losing it must not cost the turn. The
    answer degrades to `None`, which is "nobody could tell" — the runner then
    falls back to what the package proves, exactly as it did before this fact
    existed. `False` would be a different claim: "there is nothing to load"."""
    import workspace_app.api.turn_context as tc

    _filestore, builder, item_id = _build(monkeypatch, _ALL_APP_SKILLS_OFF)

    async def _boom(*_a, **_k):
        raise RuntimeError("workspace unreachable")

    monkeypatch.setattr(tc, "advertised_workspace_skills", _boom)

    ctx = await _turn(builder, item_id)

    assert ctx.skills_reachable is None


async def test_an_item_belonging_to_no_app_leaves_the_answer_unknown(monkeypatch):
    """No App and no profile: there are no declared skills to resolve and no
    per-item toggles to apply, so this layer has nothing to say. Same `None`,
    same fallback."""
    _filestore, builder, _item_id = _build(monkeypatch)

    ctx = await _turn(builder, "not-a-registered-item")

    assert ctx.skills_reachable is None


async def test_a_workspace_copy_of_a_default_off_skill_still_reaches_one(monkeypatch):
    """The two indexes an item is given are rendered by DIFFERENT rules, and the
    grant has to satisfy both. `pm/default` opts into two of the App's three
    shared skills, so `grill-me` is default-off; a workspace COPY of it is
    advertised by the workspace block (which hides only an explicit off) and
    `read_skill` loads it (it refuses only an explicit off) — while the picker
    resolver calls the copy not-effective, because a copy answers as the skill it
    copied. Asking the resolver alone withdrew a tool that was on screen and
    would have worked."""
    prefs = {"author-skill": False, "author-workflow": False}
    filestore, builder, item_id = _build(monkeypatch, prefs, pm=True)
    # The premise: with those two off, the App side of the question is already
    # "nothing". If pm/default's opt-in list ever changes, this fails here
    # rather than passing through the App branch and testing nothing.
    assert not any(s.effective for s in effective_item_skills("pm", "default", prefs, []))
    await filestore.write(
        item_id,
        "/.skill/grill-me/SKILL.md",
        b"---\nname: grill-me\ndescription: Copied.\n---\n\nBody.\n",
    )
    await filestore.write(item_id, "/.skill/grill-me/.origin", b"{}")

    ctx = await _turn(builder, item_id)

    assert ctx.skills_reachable is True


async def test_a_workflow_node_gets_the_same_answer_as_a_chat_turn(monkeypatch):
    """The fact rides the SHARED core, so both turn shapes carry it. A workflow
    node runs against the same item and its `read_skill` must follow the same
    skills — and nothing held that wiring: it could be deleted with the whole
    suite still green, because the tests only ever drove the chat shape."""
    _filestore, builder, item_id = _build(monkeypatch)

    ctx = await builder.build_workflow_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
    )

    assert ctx.skills_reachable is True


async def test_one_turn_resolves_the_item_a_fixed_number_of_times(monkeypatch):
    """Every one of these is a specstar round trip on the event loop, and
    `apps/resolve` says it plainly: the call count IS the latency (~219 ms
    measured in production for one round trip). The turn's helpers each used to fetch
    the same answers for themselves — the App slug, the profile, the skill
    toggles, the env vars — so one turn asked ten times for what cannot change
    inside it.

    Two: one `turn_facts` for the item, threaded to everything that needs it,
    plus one from the external-tools resolve. (Seven before this change, ten
    after its first draft — which is what put this test here.) A ratchet, not a
    law: if a change genuinely needs another, update the number deliberately
    rather than letting it drift back."""
    import workspace_app.api.locator as loc

    _filestore, builder, item_id = _build(monkeypatch)
    calls = {"n": 0}
    real = loc.find_work_item

    def _counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(loc, "find_work_item", _counting)

    # WITH a config: `_overhead_for` returns 0 before touching anything when
    # there is none, and two of the calls this ratchet counts live behind it.
    await _turn(builder, item_id, AgentConfig(name="ws", system_prompt="You help."))
    chat = calls["n"]
    calls["n"] = 0
    await builder.build_workflow_turn(
        item_id, agent_config=None, run_subagent=_dummy_subagent, history_messages=[]
    )

    assert (chat, calls["n"]) == (2, 2)


async def test_the_sizing_pass_is_told_what_the_runner_will_be_told(monkeypatch):
    """`context_overhead_tokens` is measured by building the same tool schemas the
    runner will send, and the history budget is derived from it. If the sizing
    pass is not given this turn's answer it measures a different tool set —
    silently, by one `read_skill` schema — and nothing failed when the argument
    was deleted."""
    from workspace_app.api.turn_context import TurnContextBuilder

    _filestore, builder, item_id = _build(monkeypatch, _ALL_APP_SKILLS_OFF)
    seen: list[Any] = []
    real = TurnContextBuilder._tools_tokens

    def _recording(self, agent_config, **kw):
        # Pass through, never restate the signature (see test_context_trim).
        seen.append(kw.get("skills_reachable", "absent"))
        return real(self, agent_config, **kw)

    monkeypatch.setattr(TurnContextBuilder, "_tools_tokens", _recording)

    # A turn with no config never sizes anything (`_overhead_for` returns 0), so
    # the sizing pass only exists to be checked when there IS one.
    ctx = await _turn(builder, item_id, AgentConfig(name="ws", system_prompt="You help."))

    assert ctx.skills_reachable is False
    assert seen == [False]
