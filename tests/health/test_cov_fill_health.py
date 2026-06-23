"""Coverage fill for health probes:

- ToolCallCheck: a streamed tool_call delta with NO function name is skipped
  (agents.py branch 85->83).
- ReplayService: a streamed tool_call fragment with an empty `arguments` field
  is skipped (replay.py branch 262->252).
"""

from __future__ import annotations

from types import SimpleNamespace

from workspace_app.health.checks import ToolCallCheck
from workspace_app.health.replay import ReplayService, ReplayToolCall
from workspace_app.resources import AgentConfig
from workspace_app.resources.conversation import Message

# ── ToolCallCheck: a nameless tool_call delta is ignored ─────────────


def _nameless_then_named_chunk():
    """One delta carrying TWO tool_calls: the first has no function name
    (skipped), the second names the probe tool."""
    nameless = SimpleNamespace(function=SimpleNamespace(name=None))
    named = SimpleNamespace(function=SimpleNamespace(name="lookup"))
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[nameless, named]))]
    )


def test_tool_call_check_skips_deltas_without_a_function_name(monkeypatch):
    """A tool_call fragment whose name is falsy is not recorded (agents.py
    85->83); the later named call still makes the check pass."""
    import litellm

    monkeypatch.setattr(litellm, "completion", lambda **kw: iter([_nameless_then_named_chunk()]))
    res = ToolCallCheck(check_id="agent-x", description="d", model="m").run()
    assert res.status == "pass"  # the named 'lookup' delta carried through


# ── ReplayService: a tool_call fragment with empty arguments ─────────


def _name_only_chunk(*, index=0, name="read_file"):
    """A streamed tool_call carrying the name but an empty `arguments` field —
    `if args_fragment:` is False, so nothing is appended (replay.py 262->252)."""
    tc = SimpleNamespace(index=index, id="c1", function=SimpleNamespace(name=name, arguments=""))
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]))]
    )


def _args_chunk(*, index=0, args):
    tc = SimpleNamespace(index=index, id=None, function=SimpleNamespace(name=None, arguments=args))
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]))]
    )


class _FakeCompletion:
    def __init__(self, chunks):
        self._chunks = chunks
        self.kwargs: dict = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return iter(self._chunks)


def test_replay_skips_empty_argument_fragments():
    """The first fragment names the tool with empty arguments (skipped); the
    next fragments stream the actual arguments. The assembled intent still
    parses cleanly."""
    chunks = [
        _name_only_chunk(name="read_file"),  # name set, arguments="" → 262->252
        _args_chunk(args='{"path": '),
        _args_chunk(args='"oven.log"}'),
    ]
    service = ReplayService(completion=_FakeCompletion(chunks))
    config = AgentConfig(name="rca", allowed_tools=[])
    messages = [
        Message(role="user", content="check the log"),
        Message(role="assistant", content="done"),
    ]

    result = service.replay_turn(messages=messages, index=1, config=config)

    assert result.tool_calls == [ReplayToolCall(name="read_file", arguments={"path": "oven.log"})]
