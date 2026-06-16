"""RepairingModel — repair a small model's malformed tool-call JSON at the
model-output boundary, BEFORE the SDK records it, so the tool runs on the
intended args and the recorded conversation stays valid (no LiteLLM re-parse
poison next turn). #76."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from workspace_app.agent.repairing_model import RepairingModel


def _fc(args: str):
    """A function_call output item (duck-typed like the openai model — both
    expose `.type`/`.name`/`.arguments` and allow in-place mutation)."""
    return NS(type="function_call", name="read_file", arguments=args)


def _done(item):
    return NS(type="response.output_item.done", item=item)


def _completed(items):
    return NS(type="response.completed", response=NS(output=items))


class _FakeInner:
    def __init__(self, events: list, response=None):
        self._events = events
        self._response = response

    async def get_response(self, *a, **k):
        return self._response

    async def stream_response(self, *a, **k):
        for e in self._events:
            yield e


async def _stream(model) -> list:
    return [c async for c in model.stream_response(None, "in", None, [], None, [], None)]


async def test_repairs_malformed_args_in_output_item_done():
    item = _fc('{"path": ./hello.md"}')  # the #76 missing-quote slip
    out = await _stream(RepairingModel(_FakeInner([_done(item)])))
    repaired = out[0].item.arguments
    assert json.loads(repaired) == json.loads(json.dumps(json.loads(repaired)))  # valid JSON
    assert "path" in json.loads(repaired)


async def test_leaves_valid_args_untouched():
    item = _fc('{"path": "ok.md"}')
    out = await _stream(RepairingModel(_FakeInner([_done(item)])))
    assert out[0].item.arguments == '{"path": "ok.md"}'  # byte-identical, no churn


async def test_unrepairable_args_become_a_valid_backstop_sentinel():
    # When args can't be parsed or repaired (here: concatenated objects), the
    # ALWAYS-ON backstop replaces them with a VALID JSON sentinel carrying the
    # raw — so nothing downstream (SDK json.loads / LiteLLM transform) crashes
    # or poisons. args_recovery later turns the sentinel into an in-band error.
    from workspace_app.agent.arg_repair import malformed_raw

    item = _fc('{"a": 1}{"b": 2}')
    out = await _stream(RepairingModel(_FakeInner([_done(item)])))
    parsed = json.loads(out[0].item.arguments)  # MUST be valid JSON now
    assert malformed_raw(parsed) == '{"a": 1}{"b": 2}'  # raw preserved for transparency


async def test_backstop_sentinel_when_repair_is_disabled(monkeypatch):
    # The user's scenario: repair toggled OFF. Even args that repair COULD have
    # fixed (a missing quote) must still be sanitized to a valid sentinel by the
    # always-on backstop — never left raw (which would crash the SDK/LiteLLM).
    import workspace_app.agent.repairing_model as rm
    from workspace_app.agent.arg_repair import malformed_raw

    monkeypatch.setattr(rm, "repair_tool_args", lambda *_a, **_k: None)  # repair "off"
    item = _fc('{"path": ./hello.md"}')
    out = await _stream(RepairingModel(_FakeInner([_done(item)])))
    parsed = json.loads(out[0].item.arguments)
    assert malformed_raw(parsed) == '{"path": ./hello.md"}'


async def test_repairs_malformed_args_in_response_completed():
    item = _fc('{"path": ./x"}')
    out = await _stream(RepairingModel(_FakeInner([_completed([item])])))
    assert "path" in json.loads(out[0].response.output[0].arguments)


async def test_ignores_non_function_call_items():
    msg = NS(type="message", content="hi")  # not a tool call → untouched
    out = await _stream(RepairingModel(_FakeInner([_done(msg)])))
    assert out[0].item is msg


async def test_passes_through_unrelated_events():
    delta = NS(type="response.output_text.delta", delta="hello")
    out = await _stream(RepairingModel(_FakeInner([delta])))
    assert out[0] is delta


async def test_get_response_delegates_to_inner():
    sentinel = NS(output=[])
    resp = await RepairingModel(_FakeInner([], response=sentinel)).get_response(
        None, "in", None, [], None, [], None
    )
    assert resp is sentinel
