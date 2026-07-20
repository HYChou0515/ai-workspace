"""The tool-output ceiling reaches the turn it is supposed to bound.

`AgentToolContext.tool_output_max_chars` is read by the guardrail on every
tool, so if any link between the operator's YAML and the per-turn context is
missing, the knob silently keeps the default and nobody finds out until a
context blows up in production.
"""

from __future__ import annotations

import workspace_app.api.app as app_mod
from workspace_app.api import create_app
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def test_create_app_threads_the_ceiling_into_the_turn_context_builder(monkeypatch):
    captured: dict[str, object] = {}
    real = app_mod.TurnContextBuilder

    def _capture(**kw):
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(app_mod, "TurnContextBuilder", _capture)
    spec = make_spec()
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        tool_output_max_chars=12_345,
    )

    assert captured["tool_output_max_chars"] == 12_345


def test_the_ceiling_reaches_the_kb_surfaces_too(monkeypatch):
    """The KB chat turn and every ask_knowledge_base sub-agent build their own
    AgentToolContext, so threading the knob only into the RCA turn would leave
    the biggest producer of tool output running at the default whatever the
    operator configured."""
    import workspace_app.api.kb_chat_routes as kb_mod

    seen: dict[str, object] = {}
    real_routes = app_mod.register_kb_chat_routes
    real_bridge = app_mod.SubagentBridge

    def _routes(*a, **kw):
        seen["routes"] = kw.get("tool_output_max_chars")
        return real_routes(*a, **kw)

    def _bridge(**kw):
        seen["bridge"] = kw.get("tool_output_max_chars")
        return real_bridge(**kw)

    monkeypatch.setattr(app_mod, "register_kb_chat_routes", _routes)
    monkeypatch.setattr(app_mod, "SubagentBridge", _bridge)
    spec = make_spec()
    create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        tool_output_max_chars=12_345,
    )

    assert seen == {"routes": 12_345, "bridge": 12_345}
    assert kb_mod.answer_question is not None  # the sub-agent path takes it as a kwarg
