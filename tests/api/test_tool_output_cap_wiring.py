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
