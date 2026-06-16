def test_trace_enabled_follows_the_env_flag(monkeypatch):
    """LLM tracing is opt-in via WORKSPACE_LLM_TRACE — truthy tokens on,
    everything else (incl. unset) off, so production logs stay quiet."""
    from workspace_app.api.llm_trace import trace_enabled

    monkeypatch.delenv("WORKSPACE_LLM_TRACE", raising=False)
    assert trace_enabled() is False
    monkeypatch.setenv("WORKSPACE_LLM_TRACE", "1")
    assert trace_enabled() is True
    monkeypatch.setenv("WORKSPACE_LLM_TRACE", "no")
    assert trace_enabled() is False


def test_build_trace_reports_explicit_true_parallel_tool_calls():
    """`parallel_tool_calls=True` reads as 'true' — the symmetric counterpart
    to the unset/false cases."""
    from workspace_app.api.llm_trace import build_trace

    t = build_trace(
        model="m",
        endpoint="e",
        tools=[],
        parallel_tool_calls=True,
        tool_choice=None,
        reasoning_effort=None,
        tool_calls=0,
        content_text="a plain answer",
    )
    assert t.parallel_tool_calls == "true"
    assert t.outcome == "text"


def test_redact_endpoint_keeps_host_drops_path_and_credentials():
    """The LLM trace records WHERE we called, never the secret: host:port
    only — no scheme, no path, no embedded credentials."""
    from workspace_app.api.llm_trace import redact_endpoint

    assert redact_endpoint("http://localhost:11434/v1") == "localhost:11434"
    assert redact_endpoint("https://proxy.internal:4000/v1/chat") == "proxy.internal:4000"
    assert redact_endpoint("http://user:secret@host:4000/v1") == "host:4000"


def test_redact_endpoint_host_without_a_port():
    """A portless endpoint keeps just the host; an unparseable one with no
    host falls back to 'default'."""
    from workspace_app.api.llm_trace import redact_endpoint

    assert redact_endpoint("https://api.openai.com/v1") == "api.openai.com"
    assert redact_endpoint("///no-host-here") == "default"


def test_redact_endpoint_default_when_unset():
    """No per-config endpoint → the trace says 'default' rather than blank,
    so a reader can tell 'inherits the runner/provider default' apart from
    a missing field."""
    from workspace_app.api.llm_trace import redact_endpoint

    assert redact_endpoint("") == "default"
    assert redact_endpoint(None) == "default"


def test_classify_outcome_labels_the_response_shape():
    """The trace's whole point: name what the model DID. A real tool call,
    plain text, or — the #69 smoking gun — text that merely LOOKS like a
    tool call but never invoked one."""
    from workspace_app.api.llm_trace import classify_outcome

    # A tool actually fired — text alongside it doesn't matter.
    assert (
        classify_outcome(tool_calls=1, content_chars=50, looks_like_tool_call=False) == "tool_call"
    )
    # No tool, plain prose answer.
    assert classify_outcome(tool_calls=0, content_chars=80, looks_like_tool_call=False) == "text"
    # No tool, but the text is a tool-call-shaped blob → #69.
    assert (
        classify_outcome(tool_calls=0, content_chars=40, looks_like_tool_call=True)
        == "text-looks-like-tool-call"
    )
    # Nothing user-visible at all.
    assert classify_outcome(tool_calls=0, content_chars=0, looks_like_tool_call=False) == "empty"


def test_text_looks_like_tool_call_detects_named_json_blob():
    """#69 heuristic: a reply that names one of the turn's tools next to a
    JSON object is the model 'describing' a call instead of making it. Plain
    prose, or JSON unrelated to any tool, is not flagged."""
    from workspace_app.api.llm_trace import text_looks_like_tool_call

    tools = ["kb_search", "read_file"]
    assert text_looks_like_tool_call('I will kb_search({"query": "voids"})', tools)
    assert text_looks_like_tool_call('kb_search\n```json\n{"query": "x"}\n```', tools)
    assert not text_looks_like_tool_call("The defect rate is about 3%.", tools)
    assert not text_looks_like_tool_call('Here is data: {"count": 5}', tools)


def test_build_trace_normalizes_unset_request_knobs_and_classifies_outcome():
    """`build_trace` turns the raw request pieces into a readable record:
    `None` knobs read as 'unset' (not blank/null), and the outcome is the
    classified response shape. A no-tool turn whose text looks like a call
    is flagged."""
    from workspace_app.api.llm_trace import build_trace

    t = build_trace(
        model="ollama_chat/qwen3:14b",
        endpoint="localhost:11434",
        tools=["kb_search"],
        parallel_tool_calls=None,
        tool_choice=None,
        reasoning_effort=None,
        tool_calls=0,
        content_text='I will kb_search({"query": "voids"})',
    )
    assert t.parallel_tool_calls == "unset"
    assert t.tool_choice == "auto (unset)"
    assert t.reasoning_effort == ""
    assert t.outcome == "text-looks-like-tool-call"


def test_build_trace_reports_explicit_false_parallel_tool_calls():
    """An explicit `parallel_tool_calls=False` (the pre-#69 behaviour) must
    show as 'false' — that's exactly the config difference the trace exists
    to surface."""
    from workspace_app.api.llm_trace import build_trace

    t = build_trace(
        model="openai/x",
        endpoint="default",
        tools=["kb_search"],
        parallel_tool_calls=False,
        tool_choice="required",
        reasoning_effort="medium",
        tool_calls=1,
        content_text="",
    )
    assert t.parallel_tool_calls == "false"
    assert t.tool_choice == "required"
    assert t.reasoning_effort == "medium"
    assert t.outcome == "tool_call"


def test_format_trace_line_is_one_line_with_the_key_fields():
    """The log line is a single grep-friendly line carrying every field an
    operator compares against a Replay."""
    from workspace_app.api.llm_trace import LlmTurnTrace, format_trace_line

    line = format_trace_line(
        LlmTurnTrace(
            model="ollama_chat/qwen3:14b",
            endpoint="localhost:11434",
            tools=["kb_search"],
            parallel_tool_calls="unset",
            tool_choice="auto (unset)",
            reasoning_effort="",
            outcome="text-looks-like-tool-call",
        )
    )
    assert "\n" not in line
    assert "model=ollama_chat/qwen3:14b" in line
    assert "endpoint=localhost:11434" in line
    assert "tools=[kb_search]" in line
    assert "parallel_tool_calls=unset" in line
    assert "outcome=text-looks-like-tool-call" in line
