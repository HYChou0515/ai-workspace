"""arg_repair — best-effort recovery of a small model's malformed tool-call
args into valid JSON, so the tool can run instead of the turn giving up (#76)."""

from __future__ import annotations

import json

from workspace_app.agent.arg_repair import repair_tool_args


def test_repairs_missing_quote_into_valid_json_object():
    # The exact small-model slip from #76: a value missing its opening quote.
    out = repair_tool_args('{"path": ./hello.md"}')
    assert out is not None
    obj = json.loads(out)  # the result parses as valid JSON now
    assert isinstance(obj, dict)
    assert "path" in obj  # the model's intended field survived


def test_returns_none_for_concatenated_objects():
    # Two objects merged (the parallel-call streaming bug) is NOT a single-object
    # repair — return None so the existing ConcatenatedToolCallsError path owns it.
    assert repair_tool_args('{"a": 1}{"b": 2}') is None


def test_never_raises_even_if_repair_lib_blows_up(monkeypatch):
    # Defensive: repair must NEVER raise into the caller (#76 — "the system must
    # not crash because a small model produced bad output"). If the repair lib
    # itself throws, fall back to None and let the normal handling take over.
    import workspace_app.agent.arg_repair as mod

    def _boom(*_a, **_k):
        raise RuntimeError("repair exploded")

    monkeypatch.setattr(mod, "repair_json", _boom)
    assert mod.repair_tool_args('{"x": 1') is None


def test_backstop_sentinel_roundtrips_the_raw_and_is_valid_json():
    import json as _json

    from workspace_app.agent.arg_repair import make_backstop_sentinel, malformed_raw

    s = make_backstop_sentinel('{"path": ./x"}')
    # the sentinel itself MUST be valid JSON so the SDK / litellm never choke on it
    parsed = _json.loads(s)
    assert isinstance(parsed, dict)
    # and it round-trips the original raw so the tool wrap can show the user
    assert malformed_raw(parsed) == '{"path": ./x"}'


def test_malformed_raw_returns_none_for_a_normal_args_dict():
    from workspace_app.agent.arg_repair import malformed_raw

    assert malformed_raw({"path": "a.csv"}) is None


def test_malformed_raw_ignores_sentinel_key_with_non_string_value():
    # Defensive: only a string payload counts as a real backstop sentinel.
    from workspace_app.agent.arg_repair import MALFORMED_ARGS_KEY, malformed_raw

    assert malformed_raw({MALFORMED_ARGS_KEY: 123}) is None


def test_python_literals_become_json_literals_not_strings():
    """The #549-shaped defect in the repair layer itself: a model that writes
    Python's `None` (or `True`/`False`) emits invalid JSON, and json_repair's
    recovery rule for an unrecognised bare token is "wrap it in quotes". That
    turns `page_from: None` into the STRING `"None"` — so the repair does not
    rescue the call, it MANUFACTURES a type error that pydantic then rejects
    (`int_parsing`), and the model burns turns guessing what we did to it.

    These three tokens have exact JSON counterparts, so translating them is not
    intent-guessing — it is the only reading that can be correct."""
    out = repair_tool_args('{"query": "x", "page_from": None, "rerank": True, "hyde": False}')
    assert out is not None
    obj = json.loads(out)
    assert obj["page_from"] is None  # not "None"
    assert obj["rerank"] is True  # not "True"
    assert obj["hyde"] is False  # not "False"


def test_python_literals_are_only_translated_as_bare_tokens():
    """A quoted "None" is a STRING the model chose deliberately — it could be a
    real filename — so it must survive untouched. Only the unquoted token, which
    cannot be valid JSON under any reading, is translated."""
    out = repair_tool_args('{"document": "None", "note": "None means nothing"}')
    assert out is not None
    obj = json.loads(out)
    assert obj["document"] == "None"
    assert obj["note"] == "None means nothing"


def test_valid_json_null_is_left_alone():
    """The well-behaved case must not regress: a model that already emits proper
    JSON `null` never reaches the repair path, and if it does, nothing changes."""
    out = repair_tool_args('{"query": "x", "page_from": null}')
    assert out is not None
    assert json.loads(out)["page_from"] is None
