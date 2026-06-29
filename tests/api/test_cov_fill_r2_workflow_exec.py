"""Cov-fill: WorkflowExecutor.drive_turn legacy-fallback / tools-None / empty-produced.

Three branches in ``api/workflow_exec.py``'s ``drive_turn`` were only reachable via
flaky live-LLM integration runs, so the local 100% gate reports them missing:

  - the ``except (ResourceIDNotFoundError, AssertionError)`` legacy fallback — a
    ``chat_key`` that does not resolve to a Conversation makes ``conv_rm.get`` raise,
    so the run falls back to the item's default chat;
  - the ``if cfg is not None and tools is not None`` FALSE edge — ``tools=None`` skips
    the tool-ceiling narrowing;
  - the ``if produced:`` empty branch in the persist callback — a turn that emits NO
    assistant/tool messages leaves ``produced=[]``, so the conversation isn't updated.

We drive the executor directly. ``create_app`` wires a fully-built ``WorkflowExecutor``
but doesn't expose it, so we monkeypatch the symbol it imports to capture the instance,
then call ``drive_turn`` against a ``ScriptedAgentRunner`` that emits only ``RunDone``.
No real LLM / docker — ScriptedAgentRunner + MockSandbox + a fresh spec.
"""

from __future__ import annotations

import workspace_app.api.app as app_mod
from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox


def _build(monkeypatch):
    """Build the full app, capturing the ``WorkflowExecutor`` ``create_app`` wires, and
    seed one runnable item. The scripted runner emits ONLY ``RunDone`` (no MessageDelta /
    ToolEnd), so the turn reducer produces an empty message list."""
    spec = make_spec()
    runner = ScriptedAgentRunner([RunDone()])
    captured: dict[str, object] = {}
    real = app_mod.WorkflowExecutor

    def _capture(**kw):
        ex = real(**kw)
        captured["ex"] = ex
        return ex

    monkeypatch.setattr(app_mod, "WorkflowExecutor", _capture)
    create_app(spec=spec, sandbox=MockSandbox(), filestore=SpecstarFileStore(spec), runner=runner)
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    executor = captured["ex"]
    return spec, executor, item_id


async def test_drive_turn_bogus_chat_key_falls_back_tools_none_and_empty_produced(monkeypatch):
    """One call hits all three branches: a bogus ``chat_key`` triggers the legacy
    fallback (``conv_rm.get`` raises ``ResourceIDNotFoundError``), ``tools=None`` skips
    the ceiling-narrowing ``if``, and the ``RunDone``-only script leaves ``produced=[]``
    so the persist callback's ``if produced:`` empty branch runs (no conversation write)."""
    _spec, executor, item_id = _build(monkeypatch)

    answer = await executor.drive_turn(item_id, "no-such-chat", "u", "hello", None)

    assert answer == ""  # no assistant message produced → empty join
    # The fallback resolved/created the item's default chat; the empty turn persisted
    # nothing onto it (the `if produced:` branch was skipped).
    _rid, conv = executor._locator.conversation_for(item_id)
    assert all(m.role != "assistant" for m in conv.messages)
