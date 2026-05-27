from workspace_app.api.litellm_runner import _build_input
from workspace_app.api.turns import history_items


class _M:
    """Duck-typed message (role + content) — both Message and KbMessage fit."""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


def test_history_items_keeps_user_assistant_text_in_order():
    msgs = [
        _M("user", "q1"),
        _M("assistant", "a1"),
        _M("tool", "tool output"),  # excluded — plumbing, not dialogue
        _M("user", "q2"),
        _M("assistant", ""),  # excluded — empty
    ]
    assert history_items(msgs, max_messages=100) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]


def test_history_items_windows_to_the_last_n():
    msgs = [_M("user", f"m{i}") for i in range(10)]
    assert [it["content"] for it in history_items(msgs, max_messages=3)] == ["m7", "m8", "m9"]


def test_build_input_is_a_plain_string_when_no_history():
    assert _build_input([], "hello") == "hello"


def test_build_input_appends_the_user_prompt_after_history():
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    assert _build_input(hist, "q2") == [*hist, {"role": "user", "content": "q2"}]
