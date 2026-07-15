import pytest

from workspace_app.api.litellm_runner import _build_input
from workspace_app.api.turns import history_items
from workspace_app.users import User


class _Dir:
    """Minimal UserDirectory for attribution tests — resolves seeded ids and
    returns a graceful empty-name placeholder for unknown ones."""

    def __init__(self, *users: User) -> None:
        self._by_id = {u.id: u for u in users}

    def get(self, user_id: str) -> User:
        return self._by_id.get(user_id, User(id=user_id, name=""))

    def find_by_handle(self, handle: str) -> User | None:
        return None

    def all_users(self) -> list[User]:
        return list(self._by_id.values())


class _UM:
    """Duck-typed message carrying an `author` (like RCA `Message`)."""

    def __init__(self, role: str, content: str, author: str | None = None) -> None:
        self.role = role
        self.content = content
        self.author = author
        self.tool_call_id = self.tool_name = self.tool_args = None
        self.error_kind = None


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


def test_build_input_inlines_images_as_a_multimodal_user_message():
    """A vision main model's turn carries attached images inline as `input_image`
    parts alongside the text, so the model sees the pixels directly with no
    `read_image` round-trip. With no history the result is still a message LIST
    (not the bare-string fast path) because the content is multimodal."""
    urls = ["data:image/png;base64,AAAA", "data:image/jpeg;base64,BBBB"]
    assert _build_input([], "what defect?", urls) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "what defect?"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,BBBB"},
            ],
        }
    ]


def test_build_input_inlines_images_after_history():
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    assert _build_input(hist, "q2", ["data:image/png;base64,AAAA"]) == [
        *hist,
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "q2"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ],
        },
    ]


def test_build_input_rejects_a_system_message_in_history():
    """#199 — defensive boundary: the SDK supplies the system prompt
    separately, so the replayed history must never carry a `system`
    item. Fail loud here instead of as an opaque provider 'system
    message must be at the beginning' error on the next call."""
    hist = [
        {"role": "user", "content": "q1"},
        {"role": "system", "content": "leaked mid-conversation"},
    ]
    with pytest.raises(AssertionError):
        _build_input(hist, "q2")


class _E:
    """A duck-typed message that can also carry an `error_kind`."""

    def __init__(self, role, content, *, error_kind=None):
        self.role = role
        self.content = content
        self.error_kind = error_kind
        self.tool_call_id = self.tool_name = self.tool_args = None


def test_history_items_folds_cancellation_marker_into_preceding_assistant():
    """#199 — a user-cancellation must NOT inject a mid-conversation
    `system` message (providers reject 'system message must be at the
    beginning'). Fold a compact marker into the preceding assistant
    turn (Cline-classic) so the model still knows its prior, partial
    answer was cut off — the partial text itself stays."""
    msgs = [
        _E("user", "explain the SPC chart"),
        _E("assistant", "The chart shows"),  # partial, kept
        _E("error", "interrupted", error_kind="cancelled"),
        _E("user", "actually, just summarize"),
    ]
    items = history_items(msgs, max_messages=100)
    assert items == [
        {"role": "user", "content": "explain the SPC chart"},
        {"role": "assistant", "content": "The chart shows\n\n[Response interrupted by user]"},
        {"role": "user", "content": "actually, just summarize"},
    ]


def test_history_items_standalone_marker_when_no_preceding_assistant():
    """#199 — when the cancel lands before any assistant text exists,
    there is nothing to fold into, so the marker becomes a standalone
    assistant message (assistant-first is always valid; only `system`
    must be first)."""
    msgs = [
        _E("user", "do the thing"),
        _E("error", "interrupted", error_kind="cancelled"),
        _E("user", "never mind, do this instead"),
    ]
    assert history_items(msgs, max_messages=100) == [
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": "[Response interrupted by user]"},
        {"role": "user", "content": "never mind, do this instead"},
    ]


def test_history_items_standalone_marker_when_cancel_follows_a_tool_output():
    """#199 — a cancel right after a tool ran leaves a
    `function_call_output` as the last item (no `role`), so the marker
    is emitted as a fresh assistant message rather than folded onto a
    tool item."""
    msgs = [
        _E("user", "open the csv"),
        _M("assistant", ""),
        _M(
            "tool",
            '{"rows": 10}',
            tool_call_id="call-1",
            tool_name="read_file",
            tool_args={"path": "a.csv"},
        ),
        _E("error", "interrupted", error_kind="cancelled"),
        _E("user", "actually summarize it"),
    ]
    items = history_items(msgs, max_messages=100)
    assert items == [
        {"role": "user", "content": "open the csv"},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "read_file",
            "arguments": '{"path": "a.csv"}',
        },
        {"type": "function_call_output", "call_id": "call-1", "output": '{"rows": 10}'},
        {"role": "assistant", "content": "[Response interrupted by user]"},
        {"role": "user", "content": "actually summarize it"},
    ]


def test_history_items_standalone_marker_when_cancellation_is_first():
    """#199 — a cancel as the very first history item produces a
    standalone assistant marker at position 0 (still not a `system`)."""
    msgs = [
        _E("error", "interrupted", error_kind="cancelled"),
        _E("user", "ok let's start over"),
    ]
    assert history_items(msgs, max_messages=100) == [
        {"role": "assistant", "content": "[Response interrupted by user]"},
        {"role": "user", "content": "ok let's start over"},
    ]


def test_history_items_collapses_consecutive_cancellations_to_one_marker():
    """#199 — back-to-back cancellations must not stack duplicate
    markers; the second is a no-op because the last item already ends
    with the marker."""
    msgs = [
        _E("user", "explain"),
        _E("assistant", "The chart"),
        _E("error", "interrupted", error_kind="cancelled"),
        _E("error", "interrupted", error_kind="cancelled"),
        _E("user", "summarize"),
    ]
    assert history_items(msgs, max_messages=100) == [
        {"role": "user", "content": "explain"},
        {"role": "assistant", "content": "The chart\n\n[Response interrupted by user]"},
        {"role": "user", "content": "summarize"},
    ]


def test_history_items_never_emits_a_system_message():
    """#199 regression — `history_items` must NEVER produce a `system`
    item: the real system prompt is prepended by the SDK, so any
    mid-conversation system breaks 'system message must be at the
    beginning'. Holds across cancellations in any position."""
    shapes = [
        [_E("assistant", "partial"), _E("error", "x", error_kind="cancelled")],
        [_E("error", "x", error_kind="cancelled"), _E("user", "q")],
        [
            _E("user", "q"),
            _E("error", "x", error_kind="cancelled"),
            _E("error", "x", error_kind="cancelled"),
        ],
    ]
    for msgs in shapes:
        items = history_items(msgs, max_messages=100)
        assert all(it.get("role") != "system" for it in items)


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


def test_history_items_attributes_user_messages_to_their_author():
    """#242 — a multi-collaborator thread must let the model tell who said
    what. When a `UserDirectory` is supplied, each user message is prefixed
    with its author as `[Name (handle)]:` (handle = email local-part)."""
    directory = _Dir(User(id="u1", name="Alice Chen", email="alice.chen@corp.com"))
    msgs = [_UM("user", "what's the root cause?", author="u1")]
    items = history_items(msgs, max_messages=100, users=directory)
    assert items == [
        {"role": "user", "content": "[Alice Chen (alice.chen)]: what's the root cause?"},
    ]


def test_history_items_labels_each_speaker_and_never_an_assistant_turn():
    """Multi-collaborator: each user message carries its own author; the
    assistant's own turns are NEVER prefixed (the agent is not a 'speaker')."""
    directory = _Dir(
        User(id="u1", name="Alice Chen", email="alice.chen@acme.test"),
        User(id="u2", name="Bob Liu", email="bob.liu@acme.test"),
    )
    msgs = [
        _UM("user", "why did reflow drift?", author="u1"),
        _UM("assistant", "Likely the oven profile.", author="agent"),
        _UM("user", "what did she mean by drift?", author="u2"),
    ]
    items = history_items(msgs, max_messages=100, users=directory)
    assert items == [
        {"role": "user", "content": "[Alice Chen (alice.chen)]: why did reflow drift?"},
        {"role": "assistant", "content": "Likely the oven profile."},
        {"role": "user", "content": "[Bob Liu (bob.liu)]: what did she mean by drift?"},
    ]


def test_history_items_handle_falls_back_to_id_without_email():
    """No email in the directory ⇒ the handle is the stable id."""
    directory = _Dir(User(id="u9", name="Eve"))
    msgs = [_UM("user", "ping", author="u9")]
    items = history_items(msgs, max_messages=100, users=directory)
    assert items == [{"role": "user", "content": "[Eve (u9)]: ping"}]


def test_history_items_without_a_directory_projects_text_verbatim():
    """Back-compat: with no `UserDirectory` (replay, or before wiring) user
    messages are projected unprefixed even when they carry an author."""
    msgs = [_UM("user", "verbatim please", author="u1")]
    assert history_items(msgs, max_messages=100) == [
        {"role": "user", "content": "verbatim please"},
    ]


def test_history_items_user_without_an_author_is_not_prefixed():
    """A user message with no author (e.g. legacy rows) is left as-is even when
    a directory is present."""
    directory = _Dir(User(id="u1", name="Alice", email="alice@acme.test"))
    msgs = [_UM("user", "anonymous", author=None)]
    assert history_items(msgs, max_messages=100, users=directory) == [
        {"role": "user", "content": "anonymous"},
    ]
