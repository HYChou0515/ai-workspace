from workspace_app.api.litellm_runner import _build_input
from workspace_app.api.turns import history_items


class _M:
    """Duck-typed message — role + content + optional tool fields. Both
    RCA Message and KbMessage fit (they expose the same field names)."""

    def __init__(
        self,
        role: str,
        content: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        tool_args: dict | None = None,
    ) -> None:
        self.role = role
        self.content = content
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.tool_args = tool_args


def test_history_items_keeps_user_assistant_text_in_order():
    msgs = [
        _M("user", "q1"),
        _M("assistant", "a1"),
        _M("user", "q2"),
    ]
    assert history_items(msgs, max_messages=100) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]


def test_history_items_reconstructs_tool_call_and_output_from_a_tool_message():
    """Regression — the May-30 export showed the model asking 'what is
    the 1st factor?' with NO memory of step-divergence having ever run.
    Reason: the persisted tool message (role='tool', tool_call_id +
    tool_name + tool_args + content=result) was dropped from history,
    AND the assistant message that produced the call has content=''
    (its 'output' was the tool_call, not text) — so the LLM saw the
    user's question and a dialogue-less gap before it.

    Reconstruct BOTH the call and its output from each persisted tool
    message: function_call (so the LLM sees what it asked for) and
    function_call_output (so it sees what came back). The empty-content
    assistant turn that the call belonged to is dropped — its info
    lives on the tool message."""
    msgs = [
        _M("user", "analyse these wafers"),
        # The assistant turn that triggered the tool call — empty content
        # because the visible output was the tool_call itself.
        _M("assistant", ""),
        _M(
            "tool",
            '{"path": "wh.csv", "wafers": 9, "rows": 1105}',
            tool_call_id="call-xyz",
            tool_name="wafer-history",
            tool_args={"wafer_ids": ["W25-A23", "W25-A24"]},
        ),
        _M("assistant", "Done — wafer-history wrote 1105 rows to wh.csv."),
        _M("user", "what is the 1st factor?"),
    ]
    items = history_items(msgs, max_messages=100)
    assert items == [
        {"role": "user", "content": "analyse these wafers"},
        {
            "type": "function_call",
            "call_id": "call-xyz",
            "name": "wafer-history",
            "arguments": '{"wafer_ids": ["W25-A23", "W25-A24"]}',
        },
        {
            "type": "function_call_output",
            "call_id": "call-xyz",
            "output": '{"path": "wh.csv", "wafers": 9, "rows": 1105}',
        },
        {"role": "assistant", "content": "Done — wafer-history wrote 1105 rows to wh.csv."},
        {"role": "user", "content": "what is the 1st factor?"},
    ]


def test_history_items_projects_empty_tool_args_as_empty_object():
    """When a persisted tool message has no usable args (model emitted
    garbage at invoke time → _map_event now stores {} instead of the
    `{"_raw": …}` sentinel it used to fabricate), the projected
    function_call carries `arguments="{}"` — a valid empty JSON
    object the SDK can hand back to the LLM. The function_call_output
    (carrying args_recovery's error string) still follows.

    Replaces the prior peel-back logic for `{"_raw": …}` sentinels.
    No production path creates that sentinel anymore (see
    `_map_event`'s except branch); the peel-back is gone with it."""
    msgs = [
        _M("user", "open the csv"),
        _M("assistant", ""),
        _M(
            "tool",
            "Extra data on `read_file`: …",
            tool_call_id="call-1",
            tool_name="read_file",
            tool_args={},
        ),
    ]
    items = history_items(msgs, max_messages=100)
    call_item = next(i for i in items if i.get("type") == "function_call")
    assert call_item["arguments"] == "{}"
    out_item = next(i for i in items if i.get("type") == "function_call_output")
    assert out_item["call_id"] == "call-1"


def test_history_items_skips_tool_messages_missing_their_call_metadata():
    """Defensive: if a tool message somehow has no `tool_call_id` /
    `tool_name` (legacy persisted before the schema settled), drop it
    quietly rather than emit malformed function_call items."""
    msgs = [
        _M("user", "q"),
        _M("tool", "orphan output", tool_call_id=None, tool_name=None),
    ]
    assert history_items(msgs, max_messages=100) == [{"role": "user", "content": "q"}]


def test_history_items_windows_to_the_last_n_messages_then_expands():
    """Windowing happens at the PERSISTED-message level (so the window
    is comprehensible to the operator: 'last 40 messages of history').
    Each tool message can still expand to 2 SDK items after the cap."""
    msgs = [_M("user", f"m{i}") for i in range(10)]
    items = history_items(msgs, max_messages=3)
    assert [it["content"] for it in items] == ["m7", "m8", "m9"]


def test_build_input_is_a_plain_string_when_no_history():
    assert _build_input([], "hello") == "hello"


def test_build_input_appends_the_user_prompt_after_history():
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    assert _build_input(hist, "q2") == [*hist, {"role": "user", "content": "q2"}]


def test_history_items_replays_a_cancellation_as_a_system_note():
    """#37 — a user-cancellation marker carries forward so the model
    knows its prior answer was cut off (the user's next message often
    relies on that context); the partial answer itself stays too."""

    class _E:
        def __init__(self, role, content, *, error_kind=None):
            self.role = role
            self.content = content
            self.error_kind = error_kind
            self.tool_call_id = self.tool_name = self.tool_args = None

    msgs = [
        _E("user", "explain the SPC chart"),
        _E("assistant", "The chart shows"),  # partial, kept
        _E("error", "interrupted", error_kind="cancelled"),
        _E("user", "actually, just summarize"),
    ]
    items = history_items(msgs, max_messages=100)
    assert items == [
        {"role": "user", "content": "explain the SPC chart"},
        {"role": "assistant", "content": "The chart shows"},
        {"role": "system", "content": "[Your previous response was interrupted by the user.]"},
        {"role": "user", "content": "actually, just summarize"},
    ]


def test_history_items_drops_system_and_max_turns_errors_from_context():
    """#37 — infra/model errors and the step-limit are human-only
    diagnostics: never replayed to the model."""

    class _E:
        def __init__(self, role, content, *, error_kind=None):
            self.role = role
            self.content = content
            self.error_kind = error_kind
            self.tool_call_id = self.tool_name = self.tool_args = None

    for kind in ("error", "max_turns"):
        msgs = [
            _E("user", "q"),
            _E("error", "APIConnectionError: refused", error_kind=kind),
            _E("user", "retry please"),
        ]
        assert history_items(msgs, max_messages=100) == [
            {"role": "user", "content": "q"},
            {"role": "user", "content": "retry please"},
        ]


def test_history_items_drops_oldest_to_fit_a_token_budget():
    """#45 — even within the message-count window, a few huge tool
    outputs can overflow the context. A token budget (≈chars/4) drops
    the OLDEST items until the replayed history fits; the newest turns
    always survive."""

    class _M2:
        def __init__(self, role, content, **kw):
            self.role = role
            self.content = content
            self.tool_call_id = kw.get("tool_call_id")
            self.tool_name = kw.get("tool_name")
            self.tool_args = kw.get("tool_args")
            self.error_kind = None

    big = "x" * 40_000  # ~10k tokens each
    msgs = [
        _M2("user", "old question"),
        _M2("assistant", big),  # ~10k tokens — should be dropped
        _M2("user", "newer question"),
        _M2("assistant", "short recent answer"),
    ]
    # Budget ~5k tokens: only the tail that fits survives.
    items = history_items(msgs, max_messages=100, max_tokens=5_000)
    contents = [it.get("content") for it in items]
    assert big not in contents  # the oversized old message is gone
    assert "short recent answer" in contents  # newest kept
    assert "newer question" in contents


def test_history_items_token_budget_zero_means_no_token_cap():
    """`max_tokens=0` keeps the existing count-only behaviour."""

    class _M2:
        def __init__(self, role, content):
            self.role = role
            self.content = content
            self.tool_call_id = self.tool_name = self.tool_args = None
            self.error_kind = None

    msgs = [_M2("user", "x" * 100_000), _M2("assistant", "ok")]
    items = history_items(msgs, max_messages=100, max_tokens=0)
    assert len(items) == 2  # nothing dropped
