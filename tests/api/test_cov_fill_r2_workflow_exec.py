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


async def test_convert_capability_stages_text_and_is_wired_onto_the_handle(monkeypatch):
    """#324: the executor's ``convert`` reads a staged upload, runs the KB parsers to text,
    and stages it at a content-coherent path — and ``wire_handle`` binds it onto ``wf`` so a
    workflow can call ``wf.convert``."""
    from workspace_app.workflow.handle import WorkflowHandle

    _spec, executor, item_id = _build(monkeypatch)
    await executor._files.write(item_id, "/uploads/notes.md", b"# Title\r\n\r\nBody.\n")

    # Direct call: a plain-text upload passes through, staged at its bare coherent name.
    out_path, kind = await executor.convert(item_id, "uploads/notes.md", "notes.md")
    assert (out_path, kind) == ("notes.md", "passthrough")
    assert await executor._files.read(item_id, "/notes.md") == b"# Title\n\nBody.\n"

    # Wired onto the handle: wire_handle binds wf._convert → executor.convert, so the
    # journaled wf.convert (which calls through that lambda) returns the staged path.
    wf = WorkflowHandle(store=executor._files, workspace_id=item_id)
    executor.wire_handle(wf, "run-1", item_id, "u", "chat-1")
    assert await wf.convert("uploads/notes.md", "notes.md") == ("notes.md", "passthrough")


async def test_send_notification_capability_dedups_over_the_store(monkeypatch):
    """#435 P5: wire_handle binds wf.send_notification → the executor's notify over the
    in-app Notification store (the send ledger). One send lands one notification; a revise
    of the same {recipient}:{topic} is deduped by the indexed ``dedup_key`` query."""
    from specstar import QB

    from workspace_app.resources import Notification
    from workspace_app.workflow.handle import WorkflowHandle

    spec, executor, item_id = _build(monkeypatch)
    wf = WorkflowHandle(store=executor._files, workspace_id=item_id)
    executor.wire_handle(wf, "run-1", item_id, "u", "chat-1")

    r1 = await wf.send_notification(
        "bob", "issue-5-overdue", name="notify", title="Issue #5 overdue"
    )
    assert r1["sent"] is True and r1["action"] == "send"

    # a revise: same recipient+topic, changed title → M1 fingerprint dedup → no re-send
    r2 = await wf.send_notification("bob", "issue-5-overdue", name="notify", title="Issue #5 STILL")
    assert r2["sent"] is False and r2["action"] == "skip"

    rm = spec.get_resource_manager(Notification)
    rows = list(rm.list_resources((QB["dedup_key"] == "bob:issue-5-overdue").build()))
    assert len(rows) == 1  # exactly one notification created, deduped over the store


async def test_wire_handle_binds_ask_llm_over_the_run_model(monkeypatch):
    """#435 P6: wire_handle binds wf.ask_llm → the executor's ILlm (create_entity's
    cross-origin match, M1-AI). collect() is blocking, so the wired call offloads it off
    the loop; with no model wired the handle stays inert (journal-only self-dedup)."""
    from collections.abc import Iterator

    from workspace_app.kb.llm import ILlm
    from workspace_app.workflow.handle import WorkflowHandle

    class _FakeLlm(ILlm):
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            self.prompts.append(prompt)
            yield "thinking", True  # reasoning chunk is dropped by collect()
            yield "2", False

    _spec, executor, item_id = _build(monkeypatch)

    # wired: wf.ask_llm forwards the prompt to the model and returns its non-reasoning text
    fake = _FakeLlm()
    executor._ask_llm = fake
    wf = WorkflowHandle(store=executor._files, workspace_id=item_id)
    executor.wire_handle(wf, "run-1", item_id, "u", "chat-1")
    assert wf.ask_llm is not None  # wired (narrows AskLlm | None for the call below)
    assert await wf.ask_llm("which one?") == "2"
    assert fake.prompts == ["which one?"]

    # inert: no model → wire_handle leaves ask_llm at the handle default (None)
    executor._ask_llm = None
    wf2 = WorkflowHandle(store=executor._files, workspace_id=item_id)
    executor.wire_handle(wf2, "run-2", item_id, "u", "chat-1")
    assert wf2.ask_llm is None
